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
| `auth.qr_login` | QR: qr_login + wait + recreate + get_me + save | 3 | 2 | true |
| `connect_disconnect` | Connect / disconnect сессии | 1 | 0 | true |
| `get_me` | Текущий пользователь (валидация сессии) | 1 | 0 | true |
| `is_user_authorized` | Проверка авторизации | 1 | 0 | true |
| `get_entity` | Resolve username / ссылки / peer | 7 | 6 | true |
| `get_input_entity` | get_input_entity() для InputPeer | 7 | 6 | true |
| `contacts.Search` | Поиск контактов / каналов | 2 | 1 | true |
| `messages.SearchGlobal` | Глобальный поиск сообщений | 120 | 108 | true |
| `channels.GetChannelRecommendations` | Рекомендации каналов | 30 | 27 | true |
| `channels.GetFullChannel` | Полные данные канала | 80 | 72 | true |
| `channels.JoinChannel` | Подписка / join канала или discussion | 30 | 27 | true |
| `channels.LeaveChannel` | Выход из канала или discussion | 30 | 27 | true |
| `channels.GetParticipant` | Проверка участника (InputPeerSelf) | 6000 | 5400 | true |
| `channels.GetParticipants` | Список участников (megagroup / lidgen) | 500 | 450 | true |
| `get_permissions` | get_permissions() для legacy Chat | 30 | 27 | true |
| `iter_messages` | Итерация сообщений (скоринг / collect) | 450 | 405 | true |
| `users.GetFullUser` | Полные данные пользователя (NewMessage sender) | 1500 | 1350 | true |
| `bot.send_message` | Bot API: send_message | 1000 | 900 | true |
| `bot.send_photo` | Bot API: send_photo | 500 | 450 | true |

> `effective_rph` при низких лимитах (`connect_disconnect`, `get_me`, `is_user_authorized`)
> округляется до 0 — учётные операции не блокируют задачи по RPH, а служат маркерами.

## Pipelines task_type_ops (порядок шагов важен)

Порядок строк ниже = порядок выполнения op (E6 продолжает retry с шага после
`payload.last_completed_step`).

### `parser_add_channel`

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

- `collect_extra_data` и `update_channel` остаются `is_enabled=false` до задач F6/F7.
- После любого изменения seed или каталога запустите `make verify-ops-catalog`
  (или `python scripts/verify_ops_catalog_seed.py --db` для сверки с PostgreSQL).
- Per-op RPH с точки зрения оператора см. в [`docs/queue-runbook.md`](queue-runbook.md).
