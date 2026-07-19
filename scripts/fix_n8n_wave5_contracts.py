# -*- coding: utf-8 -*-
"""Surgical n8n workflow contract fixes (wave 5)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
N8N = ROOT / "n8n"


def fix_stop_urls() -> None:
    targets = [
        N8N / "эндпойнты-тг-парсер-новый-prod-api-9vYHjW4m7bwn.json",
        N8N / "tg-parser-sync-новый-prod-api-AaE9mJjKA1Xi.json",
    ]
    old = "https://lidogen-balancer-tg-prod.web.oboyma.ai/discovery-api/parser/stop/"
    new = (
        "https://lidogen-balancer-tg-prod.web.oboyma.ai/discovery-api/parser/stop/"
        "={{ $json.parser_id || $json.id }}"
    )
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if old not in text:
            print(f"skip stop url (already fixed?): {path.name}")
            continue
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        print(f"fixed stop url: {path.name}")


def fix_join_request_branch() -> None:
    path = N8N / "добавление-по-ссылке-тг-новый-prod-api-eeVHtqUvbt43.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = data.get("nodes") or []
    names = {n.get("name") for n in nodes}
    if "Respond join_pending" not in names:
        nodes.append(
            {
                "id": "a1b2c3d4-join-pending-respond",
                "name": "Respond join_pending",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.1,
                "position": [480, -160],
                "parameters": {
                    "respondWith": "json",
                    "responseBody": (
                        "={{ { status: 'join_pending', "
                        "message: 'Заявка на вступление отправлена', "
                        "join_request: true, peer_id: $json.peer_id, title: $json.title } }}"
                    ),
                    "options": {},
                },
            }
        )
        data["nodes"] = nodes
    connections = data.setdefault("connections", {})
    is_closed = connections.get("Is closed") or {"main": [[], []]}
    true_branch = is_closed["main"][0] if is_closed.get("main") else []
    if not true_branch:
        true_branch = [
            {
                "node": "Respond join_pending",
                "type": "main",
                "index": 0,
            }
        ]
        false_branch = (
            is_closed["main"][1]
            if is_closed.get("main") and len(is_closed["main"]) > 1
            else []
        )
        connections["Is closed"] = {"main": [true_branch, false_branch]}
        data["connections"] = connections
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"fixed join_request branch: {path.name}")
    else:
        print(f"join_request branch already set: {path.name}")


def fix_bot_img_url() -> None:
    path = N8N / "отправка-сообщения-тг-бот-новый-prod-api-qZOgpjNvc91M.json"
    text = path.read_text(encoding="utf-8")
    # Trigger schema: keep img_url as alias display but also accept image_url
    # Code node already maps img_url -> image_url; ensure HTTP uses prepared body.
    if '"jsonBody": "={{ $json }}"' in text:
        # Prefer body from Code node "Prepare body" if present; otherwise keep.
        print(f"bot workflow already uses json body: {path.name}")
    # Rename trigger field name for docs clarity (keep id for compat)
    updated = text.replace(
        '"displayName": "img_url"',
        '"displayName": "image_url (или img_url)"',
        1,
    )
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        print(f"updated bot field label: {path.name}")
    else:
        print(f"bot field label unchanged: {path.name}")


def fix_discover_errors_note() -> None:
    path = N8N / "телеграм-поиск-новый-prod-api-C3njXogWbSrx.json"
    if not path.exists():
        print(f"missing {path.name}")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = data.get("nodes") or []
    if any(n.get("name") == "Check discover errors" for n in nodes):
        print(f"discover check already present: {path.name}")
        return
    # Add IF node after enqueue — operators must wire it; we also try to insert
    # into connections from the enqueue HTTP node.
    nodes.append(
        {
            "id": "discover-errors-if-node",
            "name": "Check discover errors",
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.2,
            "position": [900, 200],
            "parameters": {
                "conditions": {
                    "options": {
                        "caseSensitive": True,
                        "leftValue": "",
                        "typeValidation": "strict",
                        "version": 2,
                    },
                    "combinator": "and",
                    "conditions": [
                        {
                            "id": "discover-has-task",
                            "leftValue": "={{ $json.task_id }}",
                            "rightValue": "",
                            "operator": {
                                "type": "string",
                                "operation": "exists",
                                "singleValue": True,
                            },
                        }
                    ],
                },
                "options": {},
            },
        }
    )
    nodes.append(
        {
            "id": "discover-errors-fail-node",
            "name": "Fail discover enqueue",
            "type": "n8n-nodes-base.stopAndError",
            "typeVersion": 1,
            "position": [1140, 320],
            "parameters": {
                "errorMessage": (
                    "={{ 'discover enqueue failed: ' + JSON.stringify($json.errors || $json) }}"
                ),
            },
        }
    )
    data["nodes"] = nodes
    connections = data.setdefault("connections", {})
    # Wire Check discover errors outputs
    connections["Check discover errors"] = {
        "main": [
            [],  # true: keep existing downstream manually
            [
                {
                    "node": "Fail discover enqueue",
                    "type": "main",
                    "index": 0,
                }
            ],
        ]
    }
    # Attach after node named like discover enqueue if present
    for name in list(connections.keys()):
        if "discover" in name.lower() and "постав" in name.lower():
            connections[name] = {
                "main": [
                    [
                        {
                            "node": "Check discover errors",
                            "type": "main",
                            "index": 0,
                        }
                    ]
                ]
            }
            break
    data["connections"] = connections
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"added discover error check: {path.name}")


def main() -> None:
    fix_stop_urls()
    fix_join_request_branch()
    fix_bot_img_url()
    fix_discover_errors_note()


if __name__ == "__main__":
    main()
