# Каталог op-кодов и пайплайнов (op ↔ RPH)

Канонический источник: [`app_balance/queue/ops_catalog.py`](../app_balance/queue/ops_catalog.py).
Seed для БД: [`DB/A9_seed.sql`](../DB/A9_seed.sql).
Сверка согласованности: `python scripts/verify_ops_catalog_seed.py` (добавьте `--db` для PostgreSQL).

Этот документ — человекочитаемая выжимка. При расхождении приоритет у `ops_catalog.py`;
тест `tests/test_ops_catalog.py` следит, чтобы seed и этот документ не отставали.

## Формула effective_rph

```
effective_rph = floor(rph_limit × (1 − reserve_percent / 100))
```

`reserve_percent = 10` по умолчанию (schema `resource_op_types`, A9_seed). То есть
аккаунт реально использует не более 90% номинального лимита op в час.

## Resource ops (resource_op_types)

| code | назначение | rph_limit | effective_rph | is_enabled |
|------|------------|-----------|---------------|------------|
| `auth.qr_login` | QR: qr_login + wait + recreate + get_me + save | 15 | 13 | true |
| `connect_disconnect` | Connect / disconnect сессии | 150 | 135 | true |
| `get_me` | Текущий пользователь (валидация сессии) | 150 | 135 | true |
| `is_user_authorized` | Проверка авторизации | 150 | 135 | true |
| `get_entity` | Resolve username / ссылки / peer | **223** | **200** | true |
| `get_input_entity` | get_input_entity() для InputPeer | 35 | 31 | true |
| `contacts.Search` | Поиск контактов / каналов | 10 | 9 | true |
| `messages.SearchGlobal` | Глобальный поиск сообщений | 600 | 540 | true |
| `channels.GetChannelRecommendations` | Рекомендации каналов | 150 | 135 | true |
| `channels.GetFullChannel` | Полные данные канала | **112** | **100** | true |
| `channels.JoinChannel` | Подписка / join канала или discussion | **223** | **200** | true |
| `channels.LeaveChannel` | Выход из канала или discussion | 150 | 135 | true |
| `channels.GetParticipant` | Проверка участника (InputPeerSelf) | 30000 | 27000 | true |
| `channels.GetParticipants` | Список участников (megagroup / lidgen) | 2500 | 2250 | true |
| `get_permissions` | get_permissions() для legacy Chat | 150 | 135 | true |
| `iter_messages` | Итерация сообщений (скоринг / collect) | 2250 | 2025 | true |
| `users.GetFullUser` | Полные данные пользователя (NewMessage sender) | 7500 | 6750 | true |
| `bot.send_message` | Bot API: send_message | 5000 | 4500 | true |
| `bot.send_photo` | Bot API: send_photo | 2500 | 2250 | true |

> **Жирным** — op, калиброванные под **20 кан/ч** `parser_add_channel`. Остальные — **×5** от исходного базового seed.

## Pipelines task_type_ops (порядок шагов важен)

Порядок строк ниже = порядок выполнения op (E6 продолжает retry с шага после
`payload.last_completed_step`).

### `parser_add_channel`

Пропускная способность `parser_add_channel` (A14 RPH + A15 порог): до **~80% effective
RPH** на аккаунт (`min_available_resource_percent = 20%`, резерв 20%). При A14
GetFull effective=100 — до ~80 GetFull/ч (~80 кан/ч), пока не упрётесь в Telegram.
Прочие op — **×5** от базового seed
(discovery, collect, bot и т.д.).

| # | op_code | units | role |
|---|---------|-------|------|
| 1 | `get_entity` | 2 | primary |
| 2 | `channels.JoinChannel` | 2 | primary |
| 3 | `channels.GetFullChannel` | 1 | primary |
| 4 | `channels.GetParticipant` | 1 | primary |

### `move_channel`

| # | op_code | units | role |
|---|---------|-------|------|
| 1 | `channels.GetParticipant` | 1 | source |
| 2 | `get_entity` | 2 | target |
| 3 | `channels.JoinChannel` | 2 | target |
| 4 | `channels.GetFullChannel` | 1 | target |
| 5 | `channels.GetParticipant` | 1 | target |

### `collect_extra_data`

| # | op_code | units | role |
|---|---------|-------|------|
| 1 | `get_entity` | 2 | primary |
| 2 | `channels.JoinChannel` | 2 | primary |
| 3 | `channels.GetFullChannel` | 1 | primary |
| 4 | `iter_messages` | 1 | primary |
| 5 | `channels.GetParticipants` | 1 | primary |
| 6 | `channels.LeaveChannel` | 2 | primary |

### `update_channel`

| # | op_code | units | role |
|---|---------|-------|------|
| 1 | `get_entity` | 2 | primary |
| 2 | `channels.JoinChannel` | 2 | primary |
| 3 | `channels.GetFullChannel` | 1 | primary |
| 4 | `iter_messages` | 1 | primary |
| 5 | `channels.GetParticipants` | 1 | primary |
| 6 | `channels.LeaveChannel` | 2 | primary |

### `parser_remove_channel`

| # | op_code | units | role |
|---|---------|-------|------|
| 1 | `get_entity` | 2 | primary |
| 2 | `channels.GetFullChannel` | 1 | primary |
| 3 | `channels.LeaveChannel` | 2 | primary |

## Примечания

- `update_channel` включён в seed (`is_enabled=true`) — adapter-ветка F7 реализована
  (`_execute_update_channel`). `collect_extra_data` остаётся `is_enabled=false` до
  включения в рамках F6.
- После любого изменения seed или каталога запустите `make verify-ops-catalog`
  (или `python scripts/verify_ops_catalog_seed.py --db` для сверки с PostgreSQL).
- Per-op RPH с точки зрения оператора см. в [`docs/queue-runbook.md`](queue-runbook.md).
