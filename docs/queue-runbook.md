# Runbook — очередь PostgreSQL (Lidogen Telegram Balancer)

**Дата:** 2026-06-24  
**Задача:** E5 — стабильные коды `last_error`

---

## Поля ошибки в API и БД

| Поле | Где | Описание |
|------|-----|----------|
| `last_error` | `task_queue`, D10 API | Полное значение: стабильный код или код с отладочным суффиксом (`insufficient_resource:42:get_entity`) |
| `last_error_code` | D10 API (вычисляемое) | Машиночитаемый код без суффикса; для мониторинга и алертов |
| `task_attempts.error_code` | PG | Код попытки (совпадает с префиксом `last_error`) |
| `task_attempts.error_message` | PG | Полный текст исключения для отладки |

Источник истины кодов: `app_balance/queue/error_codes.py` (`ErrorCode`).

---

## Коды ошибок

### Retryable (повтор через `run_after`)

| Код | Причина | Действие worker | Что делать оператору |
|-----|---------|-----------------|----------------------|
| `flood_wait` | FloodWait от Telegram / clump | retry с задержкой из ошибки | Подождать; при частых срабатываниях снизить RPH op |
| `clump_error` | Прочая ошибка clump (не классифицирована) | retry | Проверить логи clump; при повторении — эскалация |
| `clump_not_loaded` | Parser clump не загружен | retry | Убедиться, что парсер запущен (`/parser/start`) |
| `transient_error` | Timeout / временный сбой сети | retry | Обычно проходит сам; при массовости — проверить сеть/сервер |
| `unknown_task_type:*` | Тип задачи не найден или выключен | retry → failed | Проверить seed `task_types`, включить тип |

### Permanent (немедленный `failed`)

| Код | Причина | Действие worker | Что делать оператору |
|-----|---------|-----------------|----------------------|
| `invalid_payload` | Невалидный payload задачи | failed | Исправить payload / пересоздать задачу |
| `account_not_found` | Аккаунт отсутствует в PG | failed | Синхронизировать аккаунты |
| `unsupported_task_type` | Adapter не поддерживает тип | failed | Дождаться реализации adapter-ветки |

### Postpone (отложить без расхода attempt)

| Код | Причина | Действие worker | Что делать оператору |
|-----|---------|-----------------|----------------------|
| `insufficient_resource:*` | RPH op исчерпан | postpone | Дождаться окна; проверить загрузку аккаунтов |
| `missing_availability` | Нет данных availability для op | postpone | Проверить `account_resource_usage` / seed op |
| `no_available_account` | Нет свободного аккаунта для pick | postpone | Освободить аккаунты / добавить сессии |
| `no_ops_for_role:*` | Нет enabled op для роли (source/target) | postpone | Проверить `task_type_ops` seed |
| `account_reserve_failed:*` | Не удалось зарезервировать аккаунт | postpone | Аккаунт занят другой задачей |
| `dual_account_reserve_failed:*` | Не удалось зарезервировать пару | postpone | Проверить оба аккаунта |
| `missing_dual_accounts` | Нет source/target в dual-задаче | postpone | Исправить enqueue |
| `dual_accounts_same_id` | source == target | postpone | Исправить enqueue |

### Системные

| Код | Причина | Действие worker | Что делать оператору |
|-----|---------|-----------------|----------------------|
| `watchdog:task_timeout_exceeded` | Задача зависла в `in_progress` | stuck (watchdog) | Разобрать причину зависания; см. G5 auto-retry |
| `unexpected_error` | Нетипизированное исключение | retry | Смотреть `task_attempts.error_message` и логи worker |

### Планируется (E2 — Telethon)

| Код | Ожидаемое поведение |
|-----|---------------------|
| `channel_private` | permanent — канал недоступен |
| `banned` | permanent + sync health — аккаунт заблокирован |
| `peer_flood` | retry + cooldown |

---

## D10 API

`GET /discovery-api/parser/queue/tasks/{task_id}` возвращает:

```json
{
  "last_error": "insufficient_resource:42:get_entity",
  "last_error_code": "insufficient_resource"
}
```

Для мониторинга и алертов (G4, G6) используйте **`last_error_code`**.

---

## Per-op RPH (§0.5)

Лимиты Telegram учитываются **по типу op**, а не по аккаунту целиком. Эффективный
лимит за час: `effective_rph = floor(rph_limit × (1 − reserve_percent/100))`,
`reserve_percent = 10`.

Ключевые op (полный список — в [`docs/ops-catalog.md`](ops-catalog.md)):

| op_code | rph_limit | effective_rph |
|---------|-----------|---------------|
| `get_entity` | 7 | 6 |
| `channels.JoinChannel` | 30 | 27 |
| `channels.GetFullChannel` | 80 | 72 |
| `channels.GetParticipants` | 500 | 450 |
| `iter_messages` | 450 | 405 |
| `channels.LeaveChannel` | 30 | 27 |

Операторские подсказки:

- `flood_wait` — Telegram попросил подождать; при частоте по конкретному op снижать
  его `rph_limit` (автокоррекция — будущая задача G6★).
- `insufficient_resource:<account>:<op>` — исчерпан RPH именно этого op у аккаунта;
  смотреть суффикс `last_error`, какой op уперся в лимит.
- Источник истины RPH — `app_balance/queue/ops_catalog.py`; БД заполняется из
  `DB/A9_seed.sql`. После изменения seed запускайте `make verify-ops-catalog`
  (или `python scripts/verify_ops_catalog_seed.py --db` для сверки с PostgreSQL).

---

## Связанные документы

| Документ | Назначение |
|----------|------------|
| `docs/zadachi-bloki-e-g.md` | Backlog E–G |
| `docs/ops-catalog.md` | Каталог op ↔ RPH и пайплайны task_type_ops |
| `app_balance/queue/error_codes.py` | Реестр кодов |
| `app_balance/queue/ops_catalog.py` | Канонический каталог op-кодов и RPH |
| `scripts/e2e_d12/RUNBOOK.md` | E2E приёмка MVP |
