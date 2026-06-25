# План исполнения блока G — мониторинг, логирование ошибок, автоматические уведомления

**Дата:** 2026-06-25  
**Спринт:** S5 (после закрытия блоков E ✅ и F ✅)  
**Статус:** G1 ✅, G2 ✅, G3 ✅, G4 ✅, G7 ✅; G5–G6 — не начаты  
**Связанные документы:** [`zadachi-bloki-e-g.md`](zadachi-bloki-e-g.md), [`queue-runbook.md`](queue-runbook.md), [`docs(plan)/итоговый-план-разработки.md`](plan/итоговый-план-разработки.md)

Документ описывает порядок выполнения задач **G1–G7★**, архитектуру, критерии приёмки, **параллельность работ** и открытые решения.

---

## 1. Цель блока

Observability очереди PostgreSQL и ресурсов аккаунтов (ТЗ §26): метрики, алерты при нештатных ситуациях, автоматическая реакция на паттерны ошибок (G6★) и оповещения в чат при порогах загрузки (G7★).

**Критерий закрытия S5 (блок G):**

- `GET /queue/metrics` отдаёт JSON по §26
- Алерты §26.4 работают (лог + webhook и/или Telegram)
- Watchdog опционально делает auto-retry stuck (G5)
- Детектор повторяющихся ошибок снижает RPH per-op и уведомляет разработчика (G6★)
- При 75% каналов / 100% ресурса — сообщение в чат с debounce (G7★)

---

## 2. Текущее состояние

| Компонент | Статус | Где |
|-----------|--------|-----|
| SQL VIEW §26.2 очередь (G1) | ✅ закрыто (верификация + тесты) | `DB/BD_schema.sql`, `tests/test_monitoring_views.py` |
| SQL VIEW §26.3 ресурсы (G2) | ✅ закрыто (верификация + тесты) | `DB/BD_schema.sql`, `tests/test_monitoring_views.py` |
| Стабильные коды ошибок (E5) | ✅ | `app_balance/queue/error_codes.py`, runbook |
| Запись попыток (E4) | ✅ | `task_attempts.error_code`, `error_message` |
| Watchdog stuck (C6) | ✅ только маркировка | `app_balance/queue/watchdog.py` |
| D10 API задачи | ✅ | `GET /discovery-api/parser/queue/tasks/{id}` |
| GET /queue/metrics (G3) | ✅ | `metrics_repo.py`, `GET /discovery-api/parser/queue/metrics` |
| Алерты (G4) | ✅ | `queue_monitor.py`, `alert_rules.py`, profile `monitoring` |
| Auto-retry stuck (G5) | ❌ | — |
| Детектор ошибок (G6★) | ❌ | — |
| Чат-оповещения (G7★) | ✅ | `threshold_rules.py`, Telegram dev |

**Вывод:** слой данных (~40%) готов; прикладной слой (API, job'ы, правила, notify) — предстоит реализовать.

---

## 3. Целевая архитектура

```
PostgreSQL (VIEW + task_queue + task_attempts)
        │
        ▼
  metrics_repo.py  ◄── G1/G2 VIEW
        │
        ├──► GET /queue/metrics (G3) ──► n8n / админка
        │
        ├──► alert tick (G4) ──► log ERROR + webhook
        │
        ├──► error_detector tick (G6) ──► UPDATE rph_limit + notify dev
        │
        └──► threshold_notifier tick (G7) ──► Telegram / webhook

Общий модуль: app_balance/queue/monitoring/notify.py
Планировщик: расширение queue_scheduler или queue_monitor.py (по образцу F8)
```

**Предлагаемая структура кода:**

```
app_balance/queue/monitoring/
  metrics_repo.py       # чтение VIEW → typed dataclass
  alert_rules.py        # пороги G4
  error_detector.py     # G6
  threshold_notifier.py # G7
  notify.py             # webhook / Telegram / structured log
  config.py             # env-пороги
```

---

## 4. Граф зависимостей

```
A5, A3, A4, A6 ──► G1 ──┐
                         ├──► G3 ──► G4 ──┬──► G6★
A3, A4, A6 ──► G2 ──────┘                └──► G7★

C6, E3 ──► G5   (независим от G3)

G0 (верификация G1/G2) ──► G3
```

---

## 5. Параллельность задач

### 5.1. Сводная таблица

| Задача | Можно начать после | Параллельно с | Блокирует |
|--------|-------------------|---------------|-----------|
| **G0** (верификация VIEW) | — (сразу) | G5 | G3 |
| **G1** (формальное закрытие VIEW очереди) | — | G2, G5, G0 | G3 |
| **G2** (формальное закрытие VIEW ресурсов) | — | G1, G5, G0 | G3 |
| **G3** (GET /queue/metrics) | G0 (или G1+G2) | — | G4, G6, G7 |
| **G4** (алерты §26.4) | G3 + notify-скелет | G5 | G6, G7 (частично) |
| **G5** (watchdog auto-retry) | C6, E3 (✅) | G0, G1, G2, G4-скелет | — |
| **G6★** (детектор ошибок) | G3, notify из G4 | G7 (после notify) | — |
| **G7★** (пороги загрузки) | G3, notify из G4 | G6 (после notify) | — |

### 5.2. Волны исполнения (разбивка)

> **G1/G2** — VIEW уже в схеме (A5/A8); в волнах ниже «закрытие G1/G2» = тесты и формальная приёмка в рамках **G0** или параллельно с ним.

---

**Волна 1** — нет зависимостей внутри блока G (можно стартовать сразу):

| Параллельно | Задача | Суть |
|-------------|--------|------|
| поток A | **G0** | Верификация мониторинговых VIEW |
| поток A | **G1** | Формальное закрытие VIEW очереди (тесты, preflight) |
| поток A | **G2** | Формальное закрытие VIEW ресурсов (тесты, preflight) |
| поток B | **G5** | Watchdog → опциональный auto-retry stuck |

*G0, G1, G2 — один поток (общие тесты VIEW). G5 — отдельный поток, не пересекается по файлам.*

---

**Волна 2** — разблокирована тем, что в волне 1 закрыты **G0 + G1 + G2**:

| Задача | Суть |
|--------|------|
| **G3** | `GET /queue/metrics` — единая HTTP-точка метрик |

*Если G5 из волны 1 ещё не merged — можно доделывать параллельно с G3 (G5 не блокирует G3).*

---

**Волна 3** — разблокирована тем, что в волне 2 закрыта **G3**:

| Задача | Суть |
|--------|------|
| **G4** | Алерты §26.4 + общий модуль `notify.py` (лог, webhook, debounce) |

*G4 — единственная задача волны; критический путь. Без G4 не стартуют G6/G7 (им нужен notify).*

---

**Волна 4** — разблокирована тем, что в волне 3 закрыта **G4** (в т.ч. `notify.py`, `metrics_repo.py`):

| Параллельно | Задача | Суть |
|-------------|--------|------|
| поток A | **G6★** | Детектор повторяющихся ошибок + авто-смена RPH |
| поток B | **G7★** | Оповещение в чат при 75% каналов / 100% ресурса |

*G6 и G7 — максимальный параллелизм блока G. Разные модули; merge-конфликты возможны только в compose/runbook — их в волну 5.*

---

**Волна 5** — разблокирована тем, что в волне 4 закрыты **G6 + G7** (и желательно **G5**):

| Задача | Суть |
|--------|------|
| **Финализация** | Runbook §G, `docker-compose` profile `monitoring`, чеклист закрытия S5, полный `make docker-test-safe` |

*Не отдельная карточка ТЗ — обязательный шаг перед закрытием блока.*

---

#### Сводка волна → задачи

```
Волна 1:  G0 + G1 + G2  ‖  G5
Волна 2:  G3                    ← нужны G0, G1, G2
Волна 3:  G4                    ← нужен G3
Волна 4:  G6  ‖  G7             ← нужен G4
Волна 5:  runbook + compose + приёмка
```

#### Два исполнителя (расклад по волнам)

| Волна | Исполнитель 1 | Исполнитель 2 |
|-------|---------------|---------------|
| 1 | G0 + G1 + G2 | G5 |
| 2 | G3 | (G5 доделка, если нужно) |
| 3 | G4 | — |
| 4 | G6 | G7 |
| 5 | runbook + compose | финальный pytest |

### 5.3. Диаграмма параллельности (Gantt-логика)

```
Время ──────────────────────────────────────────────────────────────►

[G0/G1/G2 verify]████████
[G5 auto-retry]   ████████                    ← параллельно с G0
                  [G3 metrics API]████████
                                    [G4 alerts]██████████
[G6 error detector]                      ████████████████  ← параллельно с G7
[G7 thresholds]                          ████████████      ← параллельно с G6
                                                      [docs/runbook]████
```

### 5.4. Максимальный параллелизм (2 агента)

| Агент 1 | Агент 2 |
|---------|---------|
| G0 → G3 → G4 → G6 | G5 → (ждёт notify) → G7 |

**Оптимум при одном исполнителе:** G0 → G3 → G4 → G5 → G6 → G7 (G5 можно вставить после G0).

**Оптимум при двух исполнителях:** см. волны 1–3; критический путь = G0 → G3 → G4 → max(G6, G7) ≈ **4–5 дней**.

### 5.5. Что нельзя параллелить

| Пара | Причина |
|------|---------|
| G3 ∥ G4 | G4 читает метрики через тот же контракт, что отдаёт G3 |
| G6/G7 до G3 | Нет единого `metrics_repo` / снимка метрик |
| G6/G7 до notify-скелета G4 | Дублирование Telegram/webhook логики |
| G4 полный ∥ G3 | Алерты без API можно прототипировать на repo, но приёмка G4 завязана на G3 |

### 5.6. Общие файлы — точки синхронизации

При параллельной работе merge делать в порядке:

1. `app_balance/queue/monitoring/notify.py` (G4)
2. `app_balance/queue/monitoring/metrics_repo.py` (G3)
3. G6 и G7 — в любом порядке
4. `docker-compose.yml`, `docs/queue-runbook.md` — последним

---

## 6. Задачи по карточкам

### G0 — Верификация G1/G2 (~2 ч)

**Статус в ТЗ:** формализует уже существующие VIEW из A5/A8.

**Критерии приёмки:**

- `scripts/preflight_test_db.py` проверяет все VIEW блока G
- `tests/test_monitoring_views.py` — smoke на shared PG
- Расширить §30.20 (`test_tz30_20_*`) на `v_accounts_overview`, `v_account_resource_summary`

**Артефакты:** тесты; при расхождении с ТЗ — точечная правка SQL.

**Ветка:** `feat/g0-verify-monitoring-views`

---

### G1. SQL views: очередь (~3 ч → закрытие верификацией)

**Deps:** A5 ✅  
**Статус:** ✅ закрыто (2026-06-25)  
**VIEW:** `v_queue_size_by_status`, `v_queue_size_by_type`, `v_queue_metrics`, `v_high_postpone_tasks`

**Приёмка:** VIEW доступны в PG; используются в G3.

**Закрыто (DoD G1):**

```
☑ 4 VIEW существуют и исполняются на PG (local greenfield + preflight)
☑ preflight: monitoring_views включает G1 (4/4 queue views)
☑ tests/test_monitoring_views.py — G1-секция green (7 тестов incl. scheduled age)
☑ test_tz30_20 — green (§30.20)
☑ oldest_queued: queued + scheduled (карточка G1) — SQL + runbook
☑ BD_schema.sql + A8_integrate_main_db.sql синхронизированы
```

**Следующий шаг:** G4 (`feat/g4-alerts`) — alert tick на базе `MetricsRepo`.

---

### G2. SQL views: resource %, cooldown (~3 ч → закрытие верификацией)

**Deps:** A6, A4, A3 ✅  
**Статус:** ✅ закрыто (2026-06-25)  
**VIEW:** `v_account_op_usage_last_hour`, `v_account_resource_summary`, `v_accounts_overview`, `v_account_error_rate_last_hour`, `v_task_type_error_rate_last_hour`

**Приёмка:** JSON G3 содержит per-op загрузку; не `hourly_limit − used`.

**Закрыто (DoD G2):**

```
☑ 5 VIEW G2 существуют и исполняются на PG (local greenfield + preflight)
☑ preflight: 5/5 resource/account VIEW в MONITORING_VIEWS (9/9 total)
☑ tests/test_monitoring_views.py — G2-секция green (8 тестов incl. column smoke)
☑ test_tz30_20b — green (§30.20 resource/cooldown)
☑ per-op §0.5: effective_rph, не hourly_limit — подтверждено тестами
☑ runbook §G2 + plan-doc статус обновлены
☑ SQL без изменений (чеклист §26.3 пройден)
```

**Следующий шаг:** G4 (`feat/g4-alerts`) — alert tick на базе `MetricsRepo`.

### G3. GET /queue/metrics (~4 ч)

**Deps:** G0, G1, G2 ✅  
**Статус:** ✅ закрыто (2026-06-25)  
**Размещение:** `app_balance/queue/monitoring/metrics_repo.py` + `standalone_discovery/discovery_api/queue/metrics.py` + роут в `parser_router.py`

**JSON-контракт (§26):**

```json
{
  "queue": {
    "total": 42,
    "by_status": {},
    "by_type": {},
    "oldest_queued_age_seconds": 120,
    "stuck_count": 0,
    "done_last_5_min": 10
  },
  "accounts": {
    "active": 5,
    "in_cooldown": 1,
    "without_resource": 2,
    "per_op": [],
    "worst_by_account": []
  },
  "alerts_preview": {
    "high_postpone_count": 3
  },
  "generated_at": "2026-06-25T12:00:00Z"
}
```

**Тесты:** `tests/test_g3_queue_metrics_api.py`, `standalone_discovery/tests/test_pg_queue_metrics.py`

**Ветка:** `feat/g3-queue-metrics-api`

**Закрыто (DoD G3):**

```
☑ metrics_repo.py читает 6 VIEW + COUNT high_postpone
☑ GET /discovery-api/parser/queue/metrics — JSON §26, auth через API key
☑ USE_PG_QUEUE=false → 503
☑ test_pg_queue_metrics.py — 4 unit-теста green
☑ test_g3_queue_metrics_api.py — 6 integration-тестов green (local PG)
☑ runbook §G3 обновлён
```

**Следующий шаг:** G4 (`feat/g4-alerts`) — alert tick на базе `MetricsRepo`.

---

### G4. Алерты §26.4 (~6 ч)

**Deps:** G3 ✅  
**Статус:** ✅ закрыто (2026-06-25)  
**Размещение:** `app_balance/queue/monitoring/{config,alert_rules,notify,queue_growth}.py`, `app_balance/queue_monitor.py`

| Правило | Источник | Порог (env) | Severity |
|---------|----------|-------------|----------|
| Рост очереди | delta `queue_size_total` | +20% / 15 мин | WARNING |
| Старая очередь | `oldest_queued_task_age_seconds` | > 3600 | WARNING |
| High postpone | `v_high_postpone_tasks` | postpone_count ≥ 10 | WARNING |
| Нет свободных аккаунтов | `v_accounts_overview` | active=0 | ERROR |
| Массовые ошибки по типу | `v_task_type_error_rate_last_hour` | rate > 50%, n ≥ 5 | ERROR |
| Массовые ошибки по аккаунту | `v_account_error_rate_last_hour` | аналогично | ERROR |
| Stuck без done | stuck > 0 и done_5min = 0 | — | ERROR |
| Очередь без выполнений | queue > 0 и done_5min = 0 | — | ERROR |

**Логирование:** structured log `level=ERROR`, поля `alert_code`, `message`, `metrics_snapshot`.

**Уведомления:** `ALERT_WEBHOOK_URL`; debounce через `ALERT_COOLDOWN_SECONDS` (default 1800).

**Тесты:** `tests/test_g4_alert_rules.py`

**Ветка:** `feat/g4-alerts`

**Закрыто (DoD G4):**

```
☑ 8 правил §26.4 в alert_rules.py, пороги из env
☑ notify.py: structured ERROR log + webhook + debounce
☑ queue_monitor.py: tick (--once для cron)
☑ fetch_alert_context() без изменения G3 API
☑ docker-compose profile monitoring + .env.example
☑ tests/test_g4_alert_rules.py — green
☑ runbook §G4 обновлён
```

**Следующий шаг:** G5 (`feat/g5-stuck-auto-retry`) или G6 (error detector).

---

### G5. Watchdog → auto-retry (~4 ч)

**Deps:** C6 ✅, E3 ✅  
**Статус:** ✅ закрыто (2026-06-25)

**Env:**

```
WATCHDOG_AUTO_RETRY_ENABLED=false   # default off в prod
WATCHDOG_AUTO_RETRY_MAX_ATTEMPTS=2
WATCHDOG_AUTO_RETRY_DELAY_SECONDS=60
```

**Политика:** после `mark_stuck_timed_out` при enabled → `scheduled` с `run_after` (логика E3); исчерпание attempts → `failed`.

**Тесты:** `tests/test_g5_watchdog_auto_retry.py` (§30.19)

**Ветка:** `feat/g5-stuck-auto-retry`

**Закрыто (DoD G5):**

```
☑ WatchdogAutoRetryConfig + mark_stuck_timed_out ветки stuck/retry/failed
☑ env WATCHDOG_AUTO_RETRY_* на queue-worker (compose + .env.example)
☑ tests/test_g5_watchdog_auto_retry.py + test_tz30_19 green
☑ runbook §G5
```

---

### G6★. Детектор повторяющихся ошибок + авто-RPH (~12–16 ч)

**Deps:** G3, G4 (notify)  
**Статус:** ✅ закрыто (2026-06-25)

#### G6a — Агрегатор (~4 ч)

VIEW `v_recurring_errors_window` — группировка по `(error_code, op_code)` из `task_attempts` + `account_resource_usage` за скользящее окно.

#### G6b — Правила (~3 ч)

| Условие | Действие |
|---------|----------|
| ≥5× `flood_wait` на op за 1 ч | `rph_limit := max(floor(rph×0.7), 2)` |
| ≥5× `peer_flood` | снизить RPH + cooldown |
| повторное срабатывание за 24 ч | `is_enabled=false` + CRITICAL alert |

#### G6c — Audit + notify (~2 ч)

Таблица `resource_limit_adjustments` или `system_events`; сообщение в `DEV_ALERT_TELEGRAM_CHAT_ID`.

#### G6d — Admin rollback (~3 ч, опционально MVP+)

`POST /queue/ops/{op_code}/reset-limits` или runbook SQL.

**Параллельность:** с G7 после merge G4.

**Ветка:** `feat/g6-error-detector`

---

### G7★. Оповещение в чат при порогах (~8–10 ч) ✅

**Deps:** G3, G4 (notify) — закрыто на `feat/g7-threshold-notify`.

| Порог ТЗ | Метрика | Реализация |
|----------|---------|------------|
| 100% ресурса | per-op exhausted | `v_account_resource_summary.any_op_exhausted` или `worst_available_percent = 0` |
| 75% каналов | суммарно по парку | новый VIEW `v_channel_capacity_usage` |

**Debounce:** тот же механизм, что G4 (`alert_type` + `scope_key`).

**Параллельность:** с G6 после merge G4.

**Ветка:** `feat/g7-threshold-notify`

---

## 7. Инфраструктура уведомлений (общая для G4/G6/G7)

**Env:**

| Переменная | Назначение |
|------------|------------|
| `MONITOR_INTERVAL_SECONDS` | интервал tick (default 120) |
| `ALERT_WEBHOOK_URL` | n8n / generic webhook |
| `DEV_ALERT_TELEGRAM_CHAT_ID` | чат разработчика |
| `BOT_TOKEN` | Telegram Bot API (уже в discovery) |
| `ALERT_COOLDOWN_SECONDS` | debounce (1800) |
| `WATCHDOG_AUTO_RETRY_ENABLED` | G5 |

**Docker-compose** (profile `monitoring`):

```yaml
queue-monitor:
  command: ["python", "-m", "app_balance.queue_monitor", "all"]
```

---

## 8. Фаза 0 — решения до G6/G7

| # | Вопрос | Предложение по умолчанию |
|---|--------|--------------------------|
| 1 | Целевой чат | `DEV_ALERT_TELEGRAM_CHAT_ID` + fallback webhook |
| 2 | Порог G6: N ошибок | 5 за 1 час |
| 3 | min RPH после снижения | 2 |
| 4 | Коэффициент снижения | ×0.7 |
| 5 | 75% каналов | суммарно по всем active аккаунтам |
| 6 | 100% ресурса | худший op (`worst_available_percent = 0`) |
| 7 | Audit | таблица `resource_limit_adjustments` |
| 8 | G5 в prod | `WATCHDOG_AUTO_RETRY_ENABLED=false` |

Все пороги — через env, без hardcode.

---

## 9. Тестирование и Definition of Done

| Задача | Тесты | §30 ТЗ |
|--------|-------|--------|
| G0/G1/G2 | `test_monitoring_views.py` | п.20 |
| G3 | API integration | п.20 |
| G4 | rules unit + webhook mock | п.20 |
| G5 | stuck→retry integration | **п.19** |
| G6 | detector + RPH change | п.20 |
| G7 | threshold + debounce | п.20 |

**Финальный прогон:** `make docker-test-safe` на shared PG (vps-101); локально — profile `local`, `756 passed` (2026-06-25).

**Чеклист закрытия блока G:**

```
☑ GET /queue/metrics — JSON §26
☑ G4: алерт queue growth / no accounts / high postpone
☑ G5: auto-retry документирован, §30.19 green
☑ G6: flood_wait → RPH↓, audit, notify
☑ G7: 75% каналов / 100% op → Telegram, debounce
☑ runbook §G в docs/queue-runbook.md
☑ docker-compose profile monitoring + G5 env на queue-worker
☑ Makefile docker-test-g / docker-monitor
☑ local PG: 756 passed (2026-06-25)
☑ shared PG vps-101: make docker-test-safe — **756 passed, 0 failed** (2026-06-25)
```

---

## 9.1. Волна 5 — финализация ✅ (2026-06-25)

Разблокирована закрытием G0–G7 (волны 1–4). Выполнено:

| Поток | Артефакты |
|-------|-----------|
| Runbook | §G overview, env-таблица, incident response — [`queue-runbook.md`](queue-runbook.md) |
| Compose/env | `WATCHDOG_AUTO_RETRY_*` на `queue-worker`, `.env.example` G5+G6, header compose |
| Makefile | `docker-monitor`, `docker-test-g` |
| Тесты | `tests/test_g_monitor_scheduler.py`; fix `test_tz30_19` (task_timeout_seconds) |
| Pytest | local PG: **756 passed**; shared PG vps-101: **756 passed**; G-subset: **19 passed** |
| Docs | Appendix D в [`zadachi-bloki-e-g.md`](zadachi-bloki-e-g.md), [`testing-shared-pg.md`](testing-shared-pg.md) §G |

---

## 10. Оценка трудозатрат

| Блок | Часы (оценка) |
|------|---------------|
| G0 + закрытие G1/G2 | 2 |
| G3 | 4 |
| G4 | 6 |
| G5 | 4 |
| G6★ | 12–16 |
| G7★ | 8–10 |
| notify + compose + runbook | 4 |
| **Итого** | **~40–46 ч** |

При двух параллельных потоках (волны 1–3): **~5–6 календарных дней**.

---

## 11. Риски

| Риск | Митигация |
|------|-----------|
| Спам алертов | debounce + cooldown |
| G6 слишком агрессивно снижает RPH | min_rph, audit, manual rollback |
| Merge-конфликты G6∥G7 | общий notify первым; compose/runbook последним |
| Legacy G6–G11 (cleanup action_queue) | **не входит** в этот блок — бэклог S6 |

---

## 12. Рекомендуемый порядок веток

```
feat/g0-verify-monitoring-views
    └──► feat/g3-queue-metrics-api
              └──► feat/g4-alerts
                        ├──► feat/g6-error-detector  ┐ параллельно
                        └──► feat/g7-threshold-notify  ┘

feat/g5-stuck-auto-retry  ← от main/feat-линии, параллельно с G0
```

---

*При расхождении с [`docs(plan)/итоговый-план-разработки.md`](plan/итоговый-план-разработки.md) приоритет у последнего.*
