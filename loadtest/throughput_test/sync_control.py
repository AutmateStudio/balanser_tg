"""Отключение / включение внешних источников синка (n8n + docker producers)."""

from __future__ import annotations

import logging
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

log = logging.getLogger("throughput.sync_control")

# Имена / подстроки workflow, которые шлют add/remove в discovery
SYNC_NAME_PATTERNS: tuple[str, ...] = (
    "tg-parser-sync",
    "vk-parser-sync",
    "добавление по ссылке тг",
    "добавление-по-ссылке-тг",
)

PRODUCER_SERVICE_NAMES: tuple[str, ...] = (
    "producer-collect",
    "producer-update",
    "producer-balancer",
)


def _import_n8n_helpers():
    """Ленивый импорт хелперов n8n Public API из репозитория."""
    repo_root = Path(__file__).resolve().parents[2]
    n8n_dir = repo_root / "n8n"
    if str(n8n_dir) not in sys.path:
        sys.path.insert(0, str(n8n_dir))
    from upload_n8n_newapi_workflows import (  # type: ignore[import-not-found]
        _api_request,
        _iter_workflow_summaries,
        _normalize_base_url,
        _activate_workflow,
    )

    return {
        "api_request": _api_request,
        "iter_summaries": _iter_workflow_summaries,
        "normalize_base_url": _normalize_base_url,
        "activate_workflow": _activate_workflow,
    }


def _name_matches(name: str) -> bool:
    lowered = (name or "").lower()
    for pat in SYNC_NAME_PATTERNS:
        if pat.lower() in lowered:
            return True
    return False


def deactivate_workflow(
    *,
    base_url: str,
    api_key: str,
    workflow_id: str,
    timeout: float = 60.0,
) -> None:
    helpers = _import_n8n_helpers()
    helpers["api_request"](
        base_url=base_url,
        api_key=api_key,
        method="POST",
        path=f"/workflows/{urllib.parse.quote(workflow_id, safe='')}/deactivate",
        timeout=timeout,
    )


def list_matching_active_workflows(
    *,
    base_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    helpers = _import_n8n_helpers()
    base = helpers["normalize_base_url"](base_url)
    summaries = helpers["iter_summaries"](base, api_key)
    matched: list[dict[str, Any]] = []
    for item in summaries:
        name = str(item.get("name") or "")
        wid = item.get("id")
        active = bool(item.get("active"))
        if not wid or not active:
            continue
        if _name_matches(name):
            matched.append({"id": str(wid), "name": name, "active": True})
    return matched


def disable_external_sync(
    *,
    n8n_base_url: str,
    n8n_api_key: str,
    skip_n8n: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    deactivated: list[dict[str, Any]] = []
    errors: list[str] = []
    if skip_n8n:
        log.info("skip-n8n: внешний sync не трогаем через API")
        return {"deactivated": [], "skipped": True, "errors": []}

    helpers = _import_n8n_helpers()
    base = helpers["normalize_base_url"](n8n_base_url)
    try:
        matched = list_matching_active_workflows(
            base_url=base, api_key=n8n_api_key
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Не удалось получить список n8n workflows: %s", exc)
        return {
            "deactivated": [],
            "skipped": False,
            "errors": [f"list_workflows: {exc}"],
        }

    for wf in matched:
        if dry_run:
            log.info("dry-run: deactivate n8n %s (%s)", wf["id"], wf["name"])
            deactivated.append({**wf, "dry_run": True})
            continue
        try:
            deactivate_workflow(
                base_url=base, api_key=n8n_api_key, workflow_id=wf["id"]
            )
            log.info("n8n deactivated: %s (%s)", wf["id"], wf["name"])
            deactivated.append(wf)
        except Exception as exc:  # noqa: BLE001
            msg = f"{wf['id']} {wf['name']}: {exc}"
            log.error("n8n deactivate failed: %s", msg)
            errors.append(msg)
    return {"deactivated": deactivated, "skipped": False, "errors": errors}


def enable_external_sync(
    *,
    n8n_base_url: str,
    n8n_api_key: str,
    workflows: list[dict[str, Any]],
    skip_n8n: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    if skip_n8n or not workflows:
        return {"activated": [], "errors": [], "skipped": skip_n8n}

    helpers = _import_n8n_helpers()
    base = helpers["normalize_base_url"](n8n_base_url)
    activated: list[dict[str, Any]] = []
    errors: list[str] = []
    for wf in workflows:
        wid = str(wf.get("id") or "")
        name = str(wf.get("name") or "")
        if not wid:
            continue
        if dry_run:
            activated.append({**wf, "dry_run": True})
            continue
        try:
            helpers["activate_workflow"](
                base_url=base, api_key=n8n_api_key, workflow_id=wid, timeout=60.0
            )
            log.info("n8n activated: %s (%s)", wid, name)
            activated.append(wf)
        except Exception as exc:  # noqa: BLE001
            msg = f"{wid} {name}: {exc}"
            log.error("n8n activate failed: %s", msg)
            errors.append(msg)
    return {"activated": activated, "errors": errors, "skipped": False}


def _docker_ps_names() -> list[str]:
    try:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("docker ps недоступен: %s", exc)
        return []
    if proc.returncode != 0:
        log.warning("docker ps rc=%s: %s", proc.returncode, proc.stderr[:300])
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def find_running_producers() -> list[str]:
    names = _docker_ps_names()
    found: list[str] = []
    for name in names:
        for svc in PRODUCER_SERVICE_NAMES:
            if svc in name:
                found.append(name)
                break
    return found


def stop_producers(
    *,
    skip: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    if skip:
        return {"stopped": [], "skipped": True}
    running = find_running_producers()
    if not running:
        log.info("producer-* контейнеры не запущены")
        return {"stopped": [], "skipped": False}
    if dry_run:
        log.info("dry-run: stop producers %s", running)
        return {"stopped": running, "dry_run": True, "skipped": False}
    stopped: list[str] = []
    errors: list[str] = []
    for name in running:
        try:
            proc = subprocess.run(
                ["docker", "stop", name],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if proc.returncode == 0:
                stopped.append(name)
                log.info("stopped producer container: %s", name)
            else:
                errors.append(f"{name}: {proc.stderr[:300]}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    return {"stopped": stopped, "errors": errors, "skipped": False}


def start_producers(
    containers: list[str],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not containers:
        return {"started": [], "errors": []}
    if dry_run:
        return {"started": containers, "dry_run": True, "errors": []}
    started: list[str] = []
    errors: list[str] = []
    for name in containers:
        try:
            proc = subprocess.run(
                ["docker", "start", name],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if proc.returncode == 0:
                started.append(name)
                log.info("started producer container: %s", name)
            else:
                errors.append(f"{name}: {proc.stderr[:300]}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
    return {"started": started, "errors": errors}
