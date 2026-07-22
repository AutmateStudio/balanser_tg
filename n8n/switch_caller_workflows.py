#!/usr/bin/env python3
"""Переключение вызывающих workflow между оригинальными discovery sub-workflow и их `*-newapi` версиями.

Узел ``Execute Workflow`` ссылается на sub-workflow по серверному ID
(``parameters.workflowId.value``). Копии ``*-newapi.json`` залиты в n8n как
отдельные workflow с именем ``… (новый prod API)`` и получают новые серверные ID.

Скрипт резолвит эти ID по имени через n8n Public API и патчит вызывающие файлы:

- ``--to-new`` — прямой путь: ссылки на оригиналы → ссылки на ``(новый prod API)``.
- ``--to-old`` — обратный путь: ссылки на ``(новый prod API)`` → оригиналы.

n8n требует, чтобы все референсы (sub-workflow в узлах Execute Workflow) были
опубликованы перед публикацией вызывающего workflow. Поэтому скрипт:

- при ``--to-new`` — публикует целевые ``(новый prod API)`` workflow ДО PUT
  вызывающих (``POST /workflows/{id}/activate``);
- при ``--to-old`` — после отката вызывающих снимает публикацию этих копий
  (``POST /workflows/{id}/deactivate``), если откат прошёл без ошибок.

Перед перезаписью каждый изменяемый файл копируется в
``n8n/backups/callers/<timestamp>/``. После локальной правки изменения
заливаются в n8n (``PUT /workflows/{id}``), если не указан ``--no-upload``.

Примеры:
  set N8N_BASE_URL=https://mokuegopasan.beget.app
  set N8N_API_KEY=your-api-key
  python n8n/switch_caller_workflows.py --to-new --dry-run

  python n8n/switch_caller_workflows.py --to-new
  python n8n/switch_caller_workflows.py --to-old

  python n8n/switch_caller_workflows.py --to-new --no-upload
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from upload_n8n_newapi_workflows import (  # noqa: E402
    DEFAULT_API_KEY_FILE,
    DEFAULT_BASE_URL,
    DEFAULT_INPUT_DIR,
    _api_request,
    _import_payload,
    _index_workflows_by_name,
    _iter_workflow_summaries,
    _load_env,
    _normalize_base_url,
    _read_api_key,
    _repo_root,
    _update_workflow,
    _activate_workflow,
)

EXECUTE_WORKFLOW_TYPE = "n8n-nodes-base.executeWorkflow"

# Цели переключения: полный серверный ID оригинального discovery sub-workflow →
# имена оригинала и его `*-newapi` копии (как они заданы в n8n).
TARGETS: dict[str, dict[str, str]] = {
    "RyoJ2daiN7LB2lUT": {
        "old_name": "отправка сообщений из очереди",
        "new_name": "отправка сообщений из очереди (новый prod API)",
    },
    "Ww3Hhp19xo2ymA3p": {
        "old_name": "отправка уведомления",
        "new_name": "отправка уведомления (новый prod API)",
    },
    "Cno7xg0nQg8DxpB2": {
        "old_name": "Телеграм поиск",
        "new_name": "Телеграм поиск (новый prod API)",
    },
}

# Вызывающие workflow-файлы, в которых правятся узлы Execute Workflow.
CALLER_FILES: tuple[str, ...] = (
    "мониторим-новые-лиды-rfwZP2B8DuZy.json",
    "обработка-сообщений-из-source_messages-aV0SMUfgejD9.json",
    "monitor-payments-expirations-DBRjzEwv28nB.json",
    "поиск-по-направлению-C3ZX5ZdFqhqH.json",
    "поиск-по-направлению-тг-2ObVjDauzM2Z.json",
)


@dataclass
class IdMapping:
    """Карта замены серверных ID для выбранного направления."""

    # source_id (как сейчас в файле) → (target_id, target_name)
    pairs: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Цели, которые не удалось зарезолвить (для отчёта об ошибках)
    unresolved: list[str] = field(default_factory=list)


@dataclass
class FileResult:
    file_name: str
    changed_nodes: int
    action: str
    workflow_id: str | None = None
    error: str | None = None


@dataclass
class PublishResult:
    """Результат публикации/снятия публикации целевого workflow."""

    name: str
    workflow_id: str
    action: str  # publish / unpublish / *-dry-run / *-skipped
    error: str | None = None


def _publish_workflow(
    base_url: str,
    api_key: str,
    workflow_id: str,
    *,
    timeout: float,
) -> None:
    """Опубликовать workflow (в n8n v1 — «активировать»)."""
    _activate_workflow(base_url, api_key, workflow_id, timeout=timeout)


def _unpublish_workflow(
    base_url: str,
    api_key: str,
    workflow_id: str,
    *,
    timeout: float,
) -> None:
    """Снять публикацию workflow (в n8n v1 — «деактивировать»)."""
    _api_request(
        base_url=base_url,
        api_key=api_key,
        method="POST",
        path=f"/workflows/{urllib.parse.quote(workflow_id, safe='')}/deactivate",
        timeout=timeout,
    )


def _resolve_newapi_targets(
    name_index: dict[str, dict[str, Any]],
) -> list[tuple[str, str]]:
    """Возвращает [(newapi_id, newapi_name)] для всех TARGETS, найденных в n8n."""
    targets: list[tuple[str, str]] = []
    for names in TARGETS.values():
        summary = name_index.get(names["new_name"])
        if summary and summary.get("id"):
            targets.append((str(summary["id"]), names["new_name"]))
    return targets


def _build_id_mapping(
    *,
    direction: str,
    name_index: dict[str, dict[str, Any]] | None,
) -> IdMapping:
    """Строит карту замен.

    Для ``--no-upload`` (name_index=None) обратный путь использует локально
    известные original ID, а для прямого пути нужен серверный ID newapi —
    он недоступен без API, поэтому такая комбинация отвергается выше по стеку.
    """
    mapping = IdMapping()

    for original_id, names in TARGETS.items():
        new_name = names["new_name"]
        old_name = names["old_name"]

        new_summary = name_index.get(new_name) if name_index is not None else None
        new_id = str(new_summary.get("id")) if new_summary else None

        if direction == "to-new":
            if not new_id:
                mapping.unresolved.append(new_name)
                continue
            # original ID → newapi ID
            mapping.pairs[original_id] = (new_id, new_name)
        else:  # to-old
            if not new_id:
                mapping.unresolved.append(new_name)
                continue
            # newapi ID → original ID
            mapping.pairs[new_id] = (original_id, old_name)

    return mapping


def _patch_nodes(
    workflow: dict[str, Any],
    mapping: IdMapping,
) -> int:
    """Заменяет ссылки в узлах Execute Workflow. Возвращает число изменённых узлов."""
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return 0

    changed = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("type") != EXECUTE_WORKFLOW_TYPE:
            continue

        parameters = node.get("parameters")
        if not isinstance(parameters, dict):
            continue
        workflow_id = parameters.get("workflowId")
        if not isinstance(workflow_id, dict):
            continue

        current = workflow_id.get("value")
        if not isinstance(current, str):
            continue

        replacement = mapping.pairs.get(current)
        if not replacement:
            continue

        target_id, target_name = replacement
        if current == target_id:
            continue  # идемпотентность

        workflow_id["value"] = target_id
        workflow_id["cachedResultUrl"] = f"/workflow/{target_id}"
        workflow_id["cachedResultName"] = target_name
        changed += 1

    return changed


def _backup_file(path: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / path.name)


def switch_callers(
    *,
    base_url: str,
    api_key: str | None,
    input_dir: Path,
    direction: str,
    dry_run: bool,
    activate: bool,
    no_upload: bool,
    timeout: float,
) -> tuple[list[FileResult], list[str], list[PublishResult]]:
    # Серверный ID newapi-копий известен только из n8n API (даже для обратного
    # пути он является источником замены), поэтому индекс имён нужен всегда,
    # кроме dry-run без ключа. --no-upload отключает лишь запись PUT/activate.
    name_index = _index_workflows_by_name(
        _iter_workflow_summaries(base_url, api_key or "")
    )

    mapping = _build_id_mapping(direction=direction, name_index=name_index)

    if mapping.unresolved:
        return [], mapping.unresolved, []

    newapi_targets = _resolve_newapi_targets(name_index)
    pub_ops: list[PublishResult] = []

    # Прямой путь: целевые newapi sub-workflow должны быть опубликованы ДО PUT
    # вызывающих, иначе n8n отклонит публикацию ссылающегося workflow.
    if direction == "to-new":
        for wid, wname in newapi_targets:
            if dry_run:
                pub_ops.append(PublishResult(wname, wid, "publish (dry-run)"))
            elif no_upload:
                pub_ops.append(PublishResult(wname, wid, "publish-skipped",
                                             "пропущено: --no-upload"))
            else:
                try:
                    _publish_workflow(base_url, api_key or "", wid, timeout=timeout)
                    pub_ops.append(PublishResult(wname, wid, "publish"))
                except RuntimeError as exc:
                    pub_ops.append(PublishResult(wname, wid, "publish", str(exc)))

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = input_dir / "backups" / "callers" / timestamp

    results: list[FileResult] = []

    for file_name in CALLER_FILES:
        path = input_dir / file_name
        try:
            if not path.is_file():
                results.append(
                    FileResult(file_name=file_name, changed_nodes=0, action="error",
                               error="файл не найден")
                )
                continue

            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("корень JSON должен быть объектом")

            changed = _patch_nodes(raw, mapping)

            if changed == 0:
                results.append(
                    FileResult(file_name=file_name, changed_nodes=0, action="skip")
                )
                continue

            if dry_run:
                results.append(
                    FileResult(file_name=file_name, changed_nodes=changed,
                               action="patch (dry-run)")
                )
                continue

            _backup_file(path, backup_dir)
            path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            if no_upload:
                results.append(
                    FileResult(file_name=file_name, changed_nodes=changed,
                               action="patch (local)")
                )
                continue

            caller_name = str(raw.get("name") or "").strip()
            caller_summary = name_index.get(caller_name) if name_index else None
            if not caller_summary:
                results.append(
                    FileResult(file_name=file_name, changed_nodes=changed,
                               action="error",
                               error=f"вызывающий workflow не найден в n8n по имени: {caller_name!r}")
                )
                continue

            workflow_id = str(caller_summary.get("id"))
            payload = _import_payload(raw)
            _update_workflow(base_url, api_key or "", workflow_id, payload, timeout=timeout)
            action = "patch+upload"

            if activate:
                _activate_workflow(base_url, api_key or "", workflow_id, timeout=timeout)
                action = "patch+upload+activate"

            results.append(
                FileResult(file_name=file_name, changed_nodes=changed,
                           action=action, workflow_id=workflow_id)
            )
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            results.append(
                FileResult(file_name=file_name, changed_nodes=0, action="error",
                           error=str(exc))
            )

    # Обратный путь: после отката вызывающих на оригиналы newapi-копии больше
    # не нужны как published — снимаем публикацию. Делаем это ПОСЛЕ цикла и
    # только при отсутствии ошибок, чтобы не оставить опубликованный вызывающий
    # со ссылкой на снятую с публикации копию.
    if direction == "to-old":
        caller_errors = sum(1 for item in results if item.error)
        for wid, wname in newapi_targets:
            if dry_run:
                pub_ops.append(PublishResult(wname, wid, "unpublish (dry-run)"))
            elif no_upload:
                pub_ops.append(PublishResult(wname, wid, "unpublish-skipped",
                                             "пропущено: --no-upload"))
            elif caller_errors:
                pub_ops.append(PublishResult(wname, wid, "unpublish-skipped",
                                             "пропущено: есть ошибки при откате вызывающих"))
            else:
                try:
                    _unpublish_workflow(base_url, api_key or "", wid, timeout=timeout)
                    pub_ops.append(PublishResult(wname, wid, "unpublish"))
                except RuntimeError as exc:
                    pub_ops.append(PublishResult(wname, wid, "unpublish", str(exc)))

    return results, [], pub_ops


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Переключить узлы Execute Workflow в вызывающих workflow между "
            "оригинальными discovery sub-workflow и их *-newapi версиями."
        ),
    )
    direction = parser.add_mutually_exclusive_group(required=True)
    direction.add_argument(
        "--to-new",
        action="store_true",
        help="Прямой путь: переключить ссылки на версии «(новый prod API)»",
    )
    direction.add_argument(
        "--to-old",
        action="store_true",
        help="Обратный путь: вернуть ссылки на оригинальные discovery workflow",
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
        "--dry-run",
        action="store_true",
        help="Только показать, что будет изменено, без записи и заливки",
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Активировать вызывающий workflow после PUT",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help=(
            "Только локальный патч + бэкап, без записи в n8n (PUT/activate). "
            "Серверные ID newapi-копий всё равно резолвятся через n8n API (read)."
        ),
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

    direction = "to-new" if args.to_new else "to-old"

    base_url = _normalize_base_url(args.base_url)

    key_file = Path(args.api_key_file)
    if not key_file.is_absolute():
        key_file = _repo_root() / key_file
    api_key = _read_api_key(args.api_key, key_file)

    if not api_key:
        print(
            "Ошибка: укажите API-ключ через --api-key, N8N_API_KEY "
            f"или файл --api-key-file (по умолчанию {DEFAULT_API_KEY_FILE}).\n"
            "Ключ нужен даже для --dry-run: серверные ID newapi-копий резолвятся "
            "по имени через n8n API.\n"
            "Ключ создаётся в n8n: Settings → n8n API "
            "(scope workflow:read, workflow:update).",
            file=sys.stderr,
        )
        return 2

    input_dir = Path(args.input_dir)
    if not input_dir.is_absolute():
        input_dir = _repo_root() / input_dir

    if not input_dir.is_dir():
        print(f"Ошибка: папка не найдена: {input_dir}", file=sys.stderr)
        return 2

    print(f"n8n: {base_url}")
    print(f"Папка: {input_dir}")
    print(f"Направление: {'→ новый prod API' if direction == 'to-new' else '→ оригиналы (откат)'}")
    if args.dry_run:
        print("Режим: dry-run (запросы PUT/activate/deactivate не выполняются)")
    if args.no_upload:
        print("Режим: --no-upload (локальный патч + бэкап, без записи в n8n)")

    results, unresolved, pub_ops = switch_callers(
        base_url=base_url,
        api_key=api_key,
        input_dir=input_dir,
        direction=direction,
        dry_run=args.dry_run,
        activate=args.activate,
        no_upload=args.no_upload,
        timeout=args.timeout,
    )

    if unresolved:
        print(
            "Ошибка: не удалось зарезолвить целевые workflow по имени в n8n:\n  - "
            + "\n  - ".join(unresolved)
            + "\nСначала залейте копии: python n8n/upload_n8n_newapi_workflows.py --update",
            file=sys.stderr,
        )
        return 2

    errors = 0

    pub_label = "Публикация референсов" if direction == "to-new" else "Снятие публикации референсов"
    if pub_ops and direction == "to-new":
        print(f"\n{pub_label}:")
        for op in pub_ops:
            if op.error:
                errors += 1
                print(f"  ERROR {op.action.upper()} {op.name} (id={op.workflow_id}): {op.error}",
                      file=sys.stderr)
            else:
                print(f"  {op.action.upper():<20} {op.name} (id={op.workflow_id})")
        print()

    total_nodes = 0
    for index, item in enumerate(results, start=1):
        if item.error:
            errors += 1
            print(
                f"[{index}/{len(results)}] ERROR {item.file_name}: {item.error}",
                file=sys.stderr,
            )
            continue
        total_nodes += item.changed_nodes
        suffix = f" → id={item.workflow_id}" if item.workflow_id else ""
        print(
            f"[{index}/{len(results)}] {item.action.upper():<22} "
            f"{item.file_name} (узлов: {item.changed_nodes}){suffix}"
        )

    if pub_ops and direction == "to-old":
        print(f"\n{pub_label}:")
        for op in pub_ops:
            if op.error:
                errors += 1
                print(f"  ERROR {op.action.upper()} {op.name} (id={op.workflow_id}): {op.error}",
                      file=sys.stderr)
            else:
                print(f"  {op.action.upper():<20} {op.name} (id={op.workflow_id})")

    patched = sum(1 for item in results if item.action.startswith("patch") and not item.error)
    skipped = sum(1 for item in results if item.action == "skip")
    published = sum(1 for op in pub_ops if op.action == "publish" and not op.error)
    unpublished = sum(1 for op in pub_ops if op.action == "unpublish" and not op.error)

    print(
        f"\nГотово: файлов с правками={patched}, без изменений={skipped}, "
        f"изменено узлов={total_nodes}, "
        f"published={published}, unpublished={unpublished}, "
        f"errors={errors}, всего файлов={len(results)}."
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
