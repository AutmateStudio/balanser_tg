#!/usr/bin/env python3
"""Загрузка мигрированных workflow (*-newapi.json) в n8n через Public API.

Сканирует папку n8n/ и отправляет только файлы, имя которых заканчивается на
``-newapi.json`` (копии после миграции на новый Discovery prod API).

Требуется API-ключ с scope workflow:create и workflow:update
(Settings → n8n API в UI).

Примеры:
  set N8N_BASE_URL=https://mokuegopasan.beget.app
  set N8N_API_KEY=your-api-key
  python n8n/upload_n8n_newapi_workflows.py --dry-run

  python n8n/upload_n8n_newapi_workflows.py --update --input-dir n8n

  python n8n/upload_n8n_newapi_workflows.py --api-key-file n8n/n8n_api.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment,misc]

DEFAULT_BASE_URL = "https://mokuegopasan.beget.app"
DEFAULT_INPUT_DIR = "n8n"
DEFAULT_API_KEY_FILE = "n8n/n8n_api.txt"
NEWAPI_SUFFIX = "-newapi.json"
PAGE_LIMIT = 250

# n8n Public API: workflow.additionalProperties=false (см. OpenAPI schema)
API_WORKFLOW_KEYS = frozenset({"name", "nodes", "connections", "settings", "staticData"})

API_WORKFLOW_SETTINGS_KEYS = frozenset(
    {
        "saveExecutionProgress",
        "saveManualExecutions",
        "saveDataErrorExecution",
        "saveDataSuccessExecution",
        "executionTimeout",
        "errorWorkflow",
        "timezone",
        "executionOrder",
        "callerPolicy",
        "callerIds",
        "timeSavedPerExecution",
        "availableInMCP",
    }
)

API_NODE_KEYS = frozenset(
    {
        "id",
        "name",
        "webhookId",
        "disabled",
        "notesInFlow",
        "notes",
        "type",
        "typeVersion",
        "executeOnce",
        "alwaysOutputData",
        "retryOnFail",
        "maxTries",
        "waitBetweenTries",
        "continueOnFail",
        "onError",
        "position",
        "parameters",
        "credentials",
    }
)


@dataclass(frozen=True)
class UploadResult:
    file_name: str
    workflow_name: str
    action: str
    workflow_id: str | None = None
    error: str | None = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_env() -> None:
    if load_dotenv is None:
        return
    for name in (".env", ".env.local", ".env.txt"):
        candidate = _repo_root() / name
        if candidate.is_file():
            load_dotenv(candidate, override=False)


def _normalize_base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    for suffix in ("/home/workflows", "/home", "/workflows"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


def _read_api_key(explicit: str | None, key_file: Path | None) -> str | None:
    if explicit:
        return explicit.strip()
    env_key = os.getenv("N8N_API_KEY")
    if env_key:
        return env_key.strip()
    if key_file and key_file.is_file():
        content = key_file.read_text(encoding="utf-8").strip()
        if content:
            return content.splitlines()[0].strip()
    return None


def _api_request(
    *,
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> Any:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{base_url}/api/v1{path}{query}"
    data = None
    headers = {
        "Accept": "application/json",
        "X-N8N-API-KEY": api_key,
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"HTTP {exc.code} {method} {path}: {detail or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Сеть недоступна для {url}: {exc.reason}") from exc

    if not raw:
        return None
    return json.loads(raw)


def _iter_workflow_summaries(base_url: str, api_key: str) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        params: dict[str, str] = {"limit": str(PAGE_LIMIT)}
        if cursor:
            params["cursor"] = cursor

        payload = _api_request(
            base_url=base_url,
            api_key=api_key,
            method="GET",
            path="/workflows",
            params=params,
        )

        if isinstance(payload, list):
            summaries.extend(item for item in payload if isinstance(item, dict))
            break

        if not isinstance(payload, dict):
            raise RuntimeError(f"Неожиданный ответ /workflows: {type(payload).__name__}")

        page = payload.get("data")
        if isinstance(page, list):
            summaries.extend(item for item in page if isinstance(item, dict))
        else:
            raise RuntimeError("Ответ /workflows не содержит массива data")

        cursor = payload.get("nextCursor")
        if not cursor:
            break

    return summaries


def _index_workflows_by_name(
    summaries: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in summaries:
        name = str(item.get("name") or "").strip()
        workflow_id = str(item.get("id") or "").strip()
        if name and workflow_id:
            index[name] = item
    return index


def _is_newapi_workflow_file(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix == ".json"
        and path.name.endswith(NEWAPI_SUFFIX)
    )


def iter_newapi_workflow_files(input_dir: Path) -> list[Path]:
    """Возвращает отсортированный список *-newapi.json в директории (без рекурсии)."""
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Папка не найдена: {input_dir}")

    files = sorted(
        (entry for entry in input_dir.iterdir() if _is_newapi_workflow_file(entry)),
        key=lambda item: item.name.casefold(),
    )
    return files


def _sanitize_settings(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"executionOrder": "v1"}
    cleaned = {
        key: raw[key]
        for key in API_WORKFLOW_SETTINGS_KEYS
        if key in raw
    }
    if not cleaned:
        return {"executionOrder": "v1"}
    if "executionOrder" not in cleaned:
        cleaned["executionOrder"] = "v1"
    return cleaned


def _sanitize_nodes(raw_nodes: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_nodes, list):
        raise ValueError("nodes должен быть массивом")

    nodes: list[dict[str, Any]] = []
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            raise ValueError(f"nodes[{index}] должен быть объектом")
        node = {key: item[key] for key in API_NODE_KEYS if key in item}
        if "id" not in node or "name" not in node or "type" not in node:
            raise ValueError(f"nodes[{index}]: требуются id, name, type")
        if "position" not in node:
            node["position"] = [0, 0]
        if "parameters" not in node:
            node["parameters"] = {}
        nodes.append(node)
    return nodes


def _import_payload(raw: dict[str, Any]) -> dict[str, Any]:
    if "name" not in raw:
        raise ValueError("В JSON отсутствует обязательное поле name")
    if "nodes" not in raw:
        raise ValueError(f"{raw.get('name')}: отсутствует поле nodes")

    payload: dict[str, Any] = {
        "name": raw["name"],
        "nodes": _sanitize_nodes(raw["nodes"]),
        "connections": raw.get("connections") if isinstance(raw.get("connections"), dict) else {},
        "settings": _sanitize_settings(raw.get("settings")),
    }

    if "staticData" in raw:
        static_data = raw["staticData"]
        if static_data is not None:
            payload["staticData"] = static_data

    return payload


def _load_workflow_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name}: некорректный JSON — {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: корень JSON должен быть объектом")
    return _import_payload(raw)


def _create_workflow(
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    result = _api_request(
        base_url=base_url,
        api_key=api_key,
        method="POST",
        path="/workflows",
        body=payload,
        timeout=timeout,
    )
    if not isinstance(result, dict):
        raise RuntimeError("POST /workflows вернул не объект")
    return result


def _update_workflow(
    base_url: str,
    api_key: str,
    workflow_id: str,
    payload: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    result = _api_request(
        base_url=base_url,
        api_key=api_key,
        method="PUT",
        path=f"/workflows/{urllib.parse.quote(workflow_id, safe='')}",
        body=payload,
        timeout=timeout,
    )
    if not isinstance(result, dict):
        raise RuntimeError(f"PUT /workflows/{workflow_id} вернул не объект")
    return result


def _activate_workflow(
    base_url: str,
    api_key: str,
    workflow_id: str,
    *,
    timeout: float,
) -> None:
    _api_request(
        base_url=base_url,
        api_key=api_key,
        method="POST",
        path=f"/workflows/{urllib.parse.quote(workflow_id, safe='')}/activate",
        timeout=timeout,
    )


def upload_newapi_workflows(
    *,
    base_url: str,
    api_key: str,
    input_dir: Path,
    update_existing: bool,
    activate: bool,
    dry_run: bool,
    timeout: float,
) -> list[UploadResult]:
    files = iter_newapi_workflow_files(input_dir)
    if not files:
        return []

    existing_by_name: dict[str, dict[str, Any]] = {}
    if not dry_run:
        existing_by_name = _index_workflows_by_name(
            _iter_workflow_summaries(base_url, api_key)
        )

    results: list[UploadResult] = []

    for path in files:
        try:
            payload = _load_workflow_file(path)
            workflow_name = str(payload["name"])
            existing = existing_by_name.get(workflow_name)

            if dry_run:
                action = "update" if existing and update_existing else "create"
                if existing and not update_existing:
                    action = "skip (exists)"
                results.append(
                    UploadResult(
                        file_name=path.name,
                        workflow_name=workflow_name,
                        action=action,
                        workflow_id=str(existing.get("id")) if existing else None,
                    )
                )
                continue

            if existing:
                if not update_existing:
                    results.append(
                        UploadResult(
                            file_name=path.name,
                            workflow_name=workflow_name,
                            action="skip",
                            workflow_id=str(existing.get("id")),
                        )
                    )
                    continue

                workflow_id = str(existing["id"])
                saved = _update_workflow(
                    base_url,
                    api_key,
                    workflow_id,
                    payload,
                    timeout=timeout,
                )
                action = "update"
            else:
                saved = _create_workflow(
                    base_url,
                    api_key,
                    payload,
                    timeout=timeout,
                )
                workflow_id = str(saved.get("id") or "")
                action = "create"

            if activate and workflow_id:
                _activate_workflow(
                    base_url,
                    api_key,
                    workflow_id,
                    timeout=timeout,
                )
                action = f"{action}+activate"

            results.append(
                UploadResult(
                    file_name=path.name,
                    workflow_name=workflow_name,
                    action=action,
                    workflow_id=workflow_id or None,
                )
            )
        except (ValueError, RuntimeError) as exc:
            results.append(
                UploadResult(
                    file_name=path.name,
                    workflow_name=path.stem,
                    action="error",
                    error=str(exc),
                )
            )

    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Загрузить в n8n только workflow-файлы *-newapi.json из папки n8n/"
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("N8N_BASE_URL", DEFAULT_BASE_URL),
        help=f"Базовый URL инстанса n8n (по умолчанию: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API-ключ n8n (или переменная окружения N8N_API_KEY)",
    )
    parser.add_argument(
        "--api-key-file",
        default=os.getenv("N8N_API_KEY_FILE", DEFAULT_API_KEY_FILE),
        help=f"Файл с API-ключом, если N8N_API_KEY не задан (по умолчанию: {DEFAULT_API_KEY_FILE})",
    )
    parser.add_argument(
        "--input-dir",
        default=os.getenv("N8N_INPUT_DIR", DEFAULT_INPUT_DIR),
        help=f"Папка с JSON workflow (по умолчанию: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Обновить workflow на сервере, если уже есть workflow с тем же name",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Активировать workflow после создания/обновления (осторожно с webhook/cron)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет загружено, без запросов POST/PUT",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("N8N_TIMEOUT", "60")),
        help="Таймаут HTTP-запроса в секундах",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _load_env()
    args = parse_args(argv)

    base_url = _normalize_base_url(args.base_url)
    key_file = Path(args.api_key_file)
    if not key_file.is_absolute():
        key_file = _repo_root() / key_file

    api_key = _read_api_key(args.api_key, key_file)
    if not api_key and not args.dry_run:
        print(
            "Ошибка: укажите API-ключ через --api-key, N8N_API_KEY "
            f"или файл --api-key-file (по умолчанию {DEFAULT_API_KEY_FILE}).\n"
            "Ключ создаётся в n8n: Settings → n8n API "
            "(scope workflow:create, workflow:update).",
            file=sys.stderr,
        )
        return 2

    input_dir = Path(args.input_dir)
    if not input_dir.is_absolute():
        input_dir = _repo_root() / input_dir

    try:
        files = iter_newapi_workflow_files(input_dir)
    except FileNotFoundError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2

    print(f"n8n: {base_url}")
    print(f"Папка: {input_dir}")
    print(f"Найдено *-newapi.json: {len(files)}")
    if args.dry_run:
        print("Режим: dry-run (запросы к API не выполняются)")
    if args.update:
        print("При совпадении name на сервере: PUT /workflows/{id}")
    else:
        print("При совпадении name на сервере: пропуск (используйте --update)")

    if not files:
        print("Файлы *-newapi.json не найдены.")
        return 0

    results = upload_newapi_workflows(
        base_url=base_url,
        api_key=api_key or "",
        input_dir=input_dir,
        update_existing=args.update,
        activate=args.activate,
        dry_run=args.dry_run,
        timeout=args.timeout,
    )

    errors = 0
    for index, item in enumerate(results, start=1):
        if item.error:
            errors += 1
            print(
                f"[{index}/{len(results)}] ERROR {item.file_name}: {item.error}",
                file=sys.stderr,
            )
            continue

        suffix = f" → id={item.workflow_id}" if item.workflow_id else ""
        print(
            f"[{index}/{len(results)}] {item.action.upper():<16} "
            f"{item.workflow_name} ({item.file_name}){suffix}"
        )

    created = sum(1 for item in results if item.action.startswith("create"))
    updated = sum(1 for item in results if item.action.startswith("update"))
    skipped = sum(1 for item in results if item.action.startswith("skip"))

    print(
        f"\nГотово: create={created}, update={updated}, skip={skipped}, "
        f"errors={errors}, всего файлов={len(results)}."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
