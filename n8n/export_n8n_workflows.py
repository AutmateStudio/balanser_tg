#!/usr/bin/env python3
"""Экспорт всех workflow из n8n через Public API в отдельные JSON-файлы.

Требуется API-ключ с scope workflow:read (Settings → n8n API в UI).

Примеры:
  set N8N_BASE_URL=https://mokuegopasan.beget.app
  set N8N_API_KEY=your-api-key
  python scripts/export_n8n_workflows.py

  python scripts/export_n8n_workflows.py \\
    --base-url https://mokuegopasan.beget.app \\
    --api-key %N8N_API_KEY% \\
    --output-dir n8n
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment,misc]

DEFAULT_BASE_URL = "https://mokuegopasan.beget.app"
DEFAULT_OUTPUT_DIR = "n8n"
PAGE_LIMIT = 250


def _load_env() -> None:
    if load_dotenv is None:
        return
    repo_root = Path(__file__).resolve().parents[1]
    for name in (".env", ".env.local", ".env.txt"):
        candidate = repo_root / name
        if candidate.is_file():
            load_dotenv(candidate, override=False)


def _normalize_base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    for suffix in ("/home/workflows", "/home", "/workflows"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


def _api_request(
    *,
    base_url: str,
    api_key: str,
    path: str,
    params: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> Any:
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{base_url}/api/v1{path}{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "X-N8N-API-KEY": api_key,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"HTTP {exc.code} для {path}: {detail or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Сеть недоступна для {url}: {exc.reason}") from exc

    if not body:
        return None
    return json.loads(body)


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


def _fetch_workflow(base_url: str, api_key: str, workflow_id: str) -> dict[str, Any]:
    payload = _api_request(
        base_url=base_url,
        api_key=api_key,
        path=f"/workflows/{urllib.parse.quote(workflow_id, safe='')}",
    )
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Неожиданный ответ /workflows/{workflow_id}: {type(payload).__name__}"
        )
    return payload


def _slugify(name: str, workflow_id: str) -> str:
    slug = re.sub(r"[^\w\-]+", "-", name.strip().lower(), flags=re.UNICODE)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        slug = "workflow"
    short_id = re.sub(r"[^\w\-]", "", workflow_id)[:12]
    return f"{slug}-{short_id}" if short_id else slug


def _export_payload(workflow: dict[str, Any]) -> dict[str, Any]:
    """Формат, совместимый с импортом workflow в n8n UI."""
    keys = (
        "name",
        "nodes",
        "connections",
        "settings",
        "staticData",
        "pinData",
        "meta",
    )
    exported = {key: workflow[key] for key in keys if key in workflow}
    if "name" not in exported:
        exported["name"] = workflow.get("name") or f"Workflow {workflow.get('id', '')}"
    return exported


def _write_workflow(output_dir: Path, workflow: dict[str, Any]) -> Path:
    workflow_id = str(workflow.get("id") or "unknown")
    name = str(workflow.get("name") or workflow_id)
    filename = f"{_slugify(name, workflow_id)}.json"
    target = output_dir / filename

    if target.exists():
        stem = target.stem
        suffix = 2
        while target.exists():
            target = output_dir / f"{stem}-{suffix}.json"
            suffix += 1

    payload = _export_payload(workflow)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Скачать все workflow из n8n Public API в папку n8n/",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("N8N_BASE_URL", DEFAULT_BASE_URL),
        help=f"Базовый URL инстанса n8n (по умолчанию: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("N8N_API_KEY"),
        help="API-ключ n8n (или переменная окружения N8N_API_KEY)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("N8N_OUTPUT_DIR", DEFAULT_OUTPUT_DIR),
        help=f"Папка для JSON-файлов (по умолчанию: {DEFAULT_OUTPUT_DIR})",
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
    api_key = args.api_key
    if not api_key:
        print(
            "Ошибка: укажите API-ключ через --api-key или переменную N8N_API_KEY.\n"
            "Создайте ключ в n8n: Settings → n8n API (scope workflow:read).",
            file=sys.stderr,
        )
        return 2

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = Path(__file__).resolve().parents[1] / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"n8n: {base_url}")
    print(f"Папка экспорта: {output_dir}")

    summaries = _iter_workflow_summaries(base_url, api_key)
    if not summaries:
        print("Workflow не найдены.")
        return 0

    saved: list[tuple[str, Path]] = []
    errors: list[str] = []

    for index, summary in enumerate(summaries, start=1):
        workflow_id = str(summary.get("id") or "")
        if not workflow_id:
            errors.append(f"#{index}: пропущен workflow без id")
            continue

        try:
            workflow = _fetch_workflow(base_url, api_key, workflow_id)
            path = _write_workflow(output_dir, workflow)
            saved.append((workflow.get("name") or workflow_id, path))
            print(f"[{index}/{len(summaries)}] {workflow.get('name') or workflow_id} → {path.name}")
        except RuntimeError as exc:
            errors.append(f"{workflow_id}: {exc}")

    print(f"\nГотово: сохранено {len(saved)} из {len(summaries)} workflow.")
    if errors:
        print("\nОшибки:", file=sys.stderr)
        for message in errors:
            print(f"  - {message}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
