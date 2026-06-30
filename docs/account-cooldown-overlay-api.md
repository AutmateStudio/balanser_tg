# Overlay PG cooldown — выходные данные API для дашборда

> Реализовано в `feat/dashboard-account-cooldown-api`.  
> Каноническая HTTP-справка: [`discovery-api-endpoints.md`](discovery-api-endpoints.md) §8.  
> ТЗ UI: [`dashboard-balancer-ui-tz.md`](dashboard-balancer-ui-tz.md) §5.3.1, §9.5.8, §9.14, §11.13.

## Назначение

При FloodWait dispatch записывает **`accounts.cooldown_until`** в PostgreSQL. Runtime clump параллельно держит **`SessionHealth.flood_until`** in-memory. Дашборду нужно одно поле «когда аккаунт снова доступен для очереди» — для этого API **мерджит** оба источника в overlay-поля.

**Требования:** `USE_PG_QUEUE=true`, инициализированный пул (`QUEUE_DATABASE_URL`). Без PG overlay-поля PG-слоя = `null` (runtime flood по-прежнему может быть заполнен).

## Эндпойнты

| Метод | Path | Overlay | `generated_at` |
|-------|------|---------|----------------|
| GET | `/discovery-api/parser/accounts/all` | да | да (корень ответа) |
| GET | `/discovery-api/parser/accounts` | да | нет |
| GET | `/discovery-api/parser/account-detail` | да (верхний уровень + `health`) | нет |

Auth: `X-API-Key`, как у остальных `/discovery-api/*`.

---

## Поля overlay (`AccountQueueOverlayFields`)

Общая модель: [`AccountQueueOverlayFields`](../standalone_discovery/discovery_api/parser_router.py) — наследуют `AccountFullSummary`, `AccountSummary`, `AccountDetail`.

| Поле | Тип JSON | Источник | Когда `null` |
|------|----------|----------|--------------|
| `queue_status` | string | PG `accounts.status` | аккаунт не в PG / PG выключен |
| `cooldown_until` | string (ISO UTC, суффикс `Z`) | PG `accounts.cooldown_until` | cooldown истёк или не был |
| `cooldown_remaining_seconds` | int | вычисление от PG | cooldown не активен |
| `available_at` | string (ISO UTC) | `max(PG cooldown, runtime flood)` | аккаунт доступен сейчас |
| `available_in_seconds` | int | вычисление от `available_at` | аккаунт доступен сейчас |
| `flood_until` | float (unix sec) | runtime `SessionHealth.flood_until` | нет in-memory flood |
| `current_task_id` | int | PG `accounts.current_task_id` | аккаунт не занят задачей |
| `last_error` | string | PG `last_error` (приоритет над runtime) | нет ошибки |
| `last_error_at` | string (ISO UTC) | PG `last_error_at` | нет ошибки |
| `is_enabled` | bool | PG `is_enabled` | аккаунт не в PG |

### Не путать: `status` vs `queue_status`

| Поле | Слой | Примеры значений | Для чего в UI |
|------|------|------------------|---------------|
| `status` | **Runtime** clump / listener | `healthy`, `flood_wait`, `offline`, `starting` | Telethon-сессия, listener |
| `queue_status` | **PG** dispatch | `active`, `cooldown`, `disabled`, `banned`, `error` | pick воркером, §16 ТЗ |

**Колонка «Освободится» / таймер FloodWait:** `available_at` или countdown из `available_in_seconds`.  
**Бейдж «В cooldown»:** `queue_status === "cooldown"` или `available_in_seconds > 0`.

### Формула `available_at`

```text
available_at = max(
  cooldown_until,          # если > now()
  datetime(flood_until)    # если flood_until > now()
)
```

Код: [`app_balance/queue/account_availability.py`](../app_balance/queue/account_availability.py) → `compute_availability()`.

---

## Полный ответ `GET /accounts/all`

```json
{
  "total": 4,
  "generated_at": "2026-06-30T00:01:55Z",
  "accounts": [
    {
      "session_name": "Client1",
      "display_name": "Client1",
      "description": "",
      "max_channels": null,
      "effective_max_channels": 500,
      "limit_source": "clump",
      "admin_blocked": false,
      "block_reason": null,
      "source": "import",
      "session_file_exists": true,
      "in_clump": true,
      "parser_id": "parser-1",
      "clump_name": "main",
      "status": "flood_wait",
      "banned": false,
      "ban_reason": null,
      "flood_remaining_seconds": 240,
      "flood_until": 1719701755.0,
      "connected": true,
      "running": true,
      "channel_count": 12,
      "queue_status": "cooldown",
      "cooldown_until": "2026-06-30T00:15:00Z",
      "cooldown_remaining_seconds": 270,
      "available_at": "2026-06-30T00:15:00Z",
      "available_in_seconds": 270,
      "current_task_id": null,
      "last_error": "flood_wait",
      "last_error_at": "2026-06-30T00:10:30Z",
      "is_enabled": true
    }
  ]
}
```

### Сценарии значений

| Ситуация | `queue_status` | `cooldown_until` | `available_at` | UI |
|----------|----------------|------------------|----------------|-----|
| Свободен | `active` | `null` | `null` | зелёный / «доступен» |
| FloodWait только PG (после рестарта API) | `cooldown` | ISO | = cooldown | таймер до ISO |
| FloodWait только runtime | `active` или `null` | `null` | из `flood_until` | таймер |
| PG cooldown > runtime flood | `cooldown` | ISO (дольше) | = PG | показывать PG |
| Занят задачей | `active` | `null` | `null` | `current_task_id` ≠ null |
| Banned | `banned` | `null` | `null` | ban UI, не таймер |
| Admin block | любой | — | — | `admin_blocked: true` (отдельно) |

---

## `GET /account-detail`

Overlay-поля дублируются на **верхнем уровне** ответа. Объект `health` сохраняется для обратной совместимости:

```json
{
  "parser_id": "parser-1",
  "session_name": "Client1",
  "display_name": "Client1",
  "queue_status": "cooldown",
  "cooldown_until": "2026-06-30T00:15:00Z",
  "available_at": "2026-06-30T00:15:00Z",
  "available_in_seconds": 270,
  "flood_until": 1719701755.0,
  "health": {
    "status": "flood_wait",
    "flood_until": 1719701755.0,
    "flood_remaining_seconds": 240,
    "flood_wait_count": 3,
    "banned": false
  }
}
```

---

## Рекомендации для фронтенда (Zod / UI)

```typescript
// Минимальные поля overlay для таблицы аккаунтов
interface AccountQueueOverlay {
  queue_status: 'active' | 'cooldown' | 'disabled' | 'banned' | 'error' | null;
  cooldown_until: string | null;       // ISO UTC
  cooldown_remaining_seconds: number | null;
  available_at: string | null;         // ISO UTC — главное для колонки «Освободится»
  available_in_seconds: number | null; // для live countdown
  flood_until: number | null;          // unix, опционально
  current_task_id: number | null;
  last_error: string | null;
  last_error_at: string | null;
  is_enabled: boolean | null;
}
```

**Countdown:** обновлять локально от `available_in_seconds` + `generated_at` (для `/accounts/all`) или перезапрашивать список каждые 30–60 с.

**Согласованность с `/queue/metrics`:** `accounts.in_cooldown` — только **число** аккаунтов в PG cooldown; per-account время — только из `/accounts/all`.

---

## Проверка на prod

```bash
KEY="$(grep ^API_KEY= standalone_discovery/.env | tail -1 | cut -d= -f2-)"
curl -sS -H "X-API-Key: $KEY" \
  "http://127.0.0.1:8100/discovery-api/parser/accounts/all" \
  | python3 -m json.tool | head -80
```

Ожидание после FloodWait на join: у затронутого аккаунта `queue_status=cooldown`, заполнены `cooldown_until`, `available_at`, `available_in_seconds`.
