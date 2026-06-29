#!/usr/bin/env python3
"""Генерация workflows-brief-desc.md из _workflows_summary.json."""
from __future__ import annotations

import json
import re
from pathlib import Path

N8N_DIR = Path(__file__).resolve().parent
SUMMARY = N8N_DIR / "_workflows_summary.json"
OUTPUT = N8N_DIR / "workflows-brief-desc.md"


def wf_id_from_file(filename: str) -> str:
    match = re.search(r"-([A-Za-z0-9]{10,})\.json$", filename)
    return match.group(1) if match else Path(filename).stem


def fmt_apis(apis: dict) -> list[str]:
    lines: list[str] = []
    labels = {
        "postgresql": "PostgreSQL",
        "llm": "LLM (OpenRouter)",
        "discovery_api": "Lidogen Discovery API",
        "vk_api": "VK API (api.vk.com)",
        "vk_scoring_service": "VK scoring/search service",
        "custom_servers": "Кастомные backend-сервисы",
        "internal_n8n_webhooks": "Webhook n8n (mokuegopasan.beget.app)",
        "telegram": "Telegram",
        "instagram_api": "Instagram Graph API",
        "lidogen_site": "Lidogen site (gragipemuse.beget.app)",
        "n8n_data_tables": "n8n Data Tables",
        "google_forms": "Google Forms (ссылки)",
        "vk_oauth": "VK OAuth",
    }
    for key, value in apis.items():
        label = labels.get(key, key)
        if value is True:
            lines.append(f"  - **{label}**")
        elif isinstance(value, list):
            lines.append(f"  - **{label}:**")
            for item in value:
                lines.append(f"    - {item}")
        elif value:
            lines.append(f"  - **{label}:** {value}")
    return lines or ["  - нет"]


def main() -> None:
    items = json.loads(SUMMARY.read_text(encoding="utf-8"))
    lines = [
        "# Краткое описание n8n workflow",
        "",
        "Всего workflow: **72**. Источник: экспорт с `https://mokuegopasan.beget.app`.",
        "",
        "Для каждого workflow указаны: назначение, тип запуска, связи с другими workflow, внешние API.",
        "",
        "> Связи «вызывается из» построены по узлам `Execute Workflow` в экспортированных JSON. "
        "Workflow, вызываемые только вручную или из неэкспортированных сценариев, могут не иметь входящих ссылок.",
        "",
        "---",
        "",
    ]

    for index, item in enumerate(items, start=1):
        wf_id = wf_id_from_file(item["file_name"])
        lines.append(f"## {index}. {item['display_name']}")
        lines.append("")
        lines.append(f"- **Файл:** `{item['file_name']}`")
        lines.append(f"- **ID:** `{wf_id}`")
        lines.append(f"- **Тип запуска:** {item['trigger_type']}")
        lines.append(f"- **Что делает:** {item['purpose']}")
        lines.append("")

        calls = item.get("calls_other_workflows") or []
        if calls:
            lines.append("- **Вызывает workflow:**")
            for name in calls:
                lines.append(f"  - {name}")
        else:
            lines.append("- **Вызывает workflow:** нет")

        callers = item.get("called_by") or []
        if callers:
            lines.append("- **Вызывается из workflow:**")
            for name in callers:
                lines.append(f"  - {name}")
        else:
            lines.append("- **Вызывается из workflow:** не найдено в экспорте")

        apis = item.get("external_apis") or {}
        lines.append("- **Сторонние API / интеграции:**")
        lines.extend(fmt_apis(apis))

        lines.append("")
        lines.append("---")
        lines.append("")

    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
