# ТЗ: вкладка «Балансировщик» в админ-дашборде

**Дополнение к:** «Load Balancer for Telegram Channels» ([`docs(plan)/tz-extract.txt`](../docs(plan)/tz-extract.txt), §5.9, §6, §8, §26, §30)  
**Назначение:** веб-интерфейс для **настройки**, **наблюдения** и **ручного управления** PG-балансировщиком и связанными Telegram-аккаунтами в `standalone_discovery`.

> **Самодостаточность:** документ содержит **все требования к UI**, **полный справочник HTTP API** (§9), **контракты данных очереди** (§9.5) и **overlay PG cooldown** (§5.3.1, §11.13). Дополнительно: [`account-cooldown-overlay-api.md`](account-cooldown-overlay-api.md).

**Структура документа:**

| § | Раздел |
|---|--------|
| 1–2 | Цель, размещение в дашборде |
| 3 | Общие требования |
| 4 | UI вкладка «Очередь» + §4.6 API |
| 5 | UI вкладка «Аккаунты» + §5.3.1 overlay PG + §5.7 API |
| 6 | UI вкладка «RPH» + §6.6 API |
| 7–8 | НФТ и критерии приёмки |
| 9.1–9.4 | Prod URL, BFF, маппинг UI→API, TBI |
| 9.5 | Контракт PG-очереди (JSON, ошибки, RPH, overlay аккаунтов §9.5.8) |
| 9.7–9.16 | Полный справочник всех HTTP-эндпойнтов с curl |
| 11.13 | Контракт overlay PG cooldown (входящие данные `/accounts/*`) |
| 10–14 | Зафиксированные решения, контракты, UX, интеграция, TBI |

---

## 1. Цель и контекст

Основное ТЗ описывает **бэкенд** очереди задач, диспетчер, учёт ресурса и мониторинг (§26). Проблемы должны быть **видимы оператору**, а не только в БД (§26.5 — в том числе «уведомление в админке»).

Данное дополнение фиксирует **пользовательский интерфейс** — раздел дашборда, который:

- показывает метрики очереди и аккаунтов в формате §26.2–§26.3;
- даёт доступ к списку задач и их состоянию (§9, §13);
- позволяет управлять Telegram-аккаунтами (§6, QR-авторизация discovery-api);
- позволяет настраивать лимиты пропускной способности (**RPH**) на уровне **типов задач / API-операций**, понятных оператору, а не на уровне отдельных Telethon RPC.

**Вне scope вкладки:** контур поиска `/discover` (§2.1), изменение логики scoring, прямое управление clump без API.

---

## 2. Размещение в продукте

В админ-дашборде платформы добавляется **верхнеуровневая вкладка «Балансировщик»**.

Она объединяет:

1. **презентацию** состояния PG-очереди и ресурсов аккаунтов;
2. **настройки** типов задач и лимитов RPH;
3. **операции** с Telegram-аккаунтами (QR, статус, блокировка — через существующие discovery-api эндпойнты).

Структура:

```
Дашборд
└── Балансировщик
    ├── Очередь
    ├── Аккаунты
    └── RPH
```

---

## 3. Общие требования к вкладке «Балансировщик»

| Требование | Описание |
|------------|----------|
| Источник данных | PostgreSQL-очередь (`USE_PG_QUEUE=true`); при выключенной очереди — понятное состояние «PG-очередь недоступна» (аналог HTTP 503) |
| API | **§9** — prod URL, аутентификация, контракт очереди, все эндпойнты с curl |
| Обновление | Автообновление снимка метрик (рекомендуется 15–30 с) + ручное «Обновить» |
| Алерты | Блок «Проблемы» по правилам §26.4 (рост очереди, stuck, high postpone, нет активных аккаунтов и т.д.); данные из `alerts_preview` и/или G4 |
| Безопасность | Доступ только авторизованным операторам; вызовы discovery-api с `X-API-Key` |
| Язык UI | Русский |

---

## 4. Вкладка «Очередь»

### 4.1. Назначение

Оперативный обзор **состояния очереди задач** (§5.4, §9, §26.2): объём, состав, динамика, проблемные задачи.

### 4.2. Сводные показатели (карточки / KPI)

Отображать метрики §26.2 (маппинг на JSON G3):

| Поле UI | Поле API (`GET …/queue/metrics`) | Описание |
|---------|------------|----------|
| Всего в очереди | `queue.total` | Активные задачи (не terminal) |
| По статусам | `queue.by_status` | `queued`, `scheduled`, `in_progress`, `retry`, `failed`, `stuck`, … |
| По типам | `queue.by_type` | Разбивка по `task_types.code` |
| В работе | `queue.by_status.in_progress` | Задачи в статусе in_progress |
| Зависшие | `queue.stuck_count` | Превышен `task_timeout_seconds` |
| Ошибки | `queue.by_status.failed` | Terminal failed |
| На повторе | `queue.by_status.retry` | Ожидают retry |
| Часто отложенные | `alerts_preview.high_postpone_count` | Задачи с высоким `postpone_count` (порог — §26.4) |
| Выполнено за 5 мин | `queue.done_last_5_min` | Пропускная способность |
| Возраст старейшей | `queue.oldest_queued_age_seconds` | Секунды; oldest queued/scheduled task |

Дополнительно:

- **Размер очереди** — числовой и/или sparkline за последний час (если есть история снимков).
- **Состав задач** — pie/bar: по типам и по статусам.

### 4.3. Таблица «Последние задачи»

Список последних N задач (рекомендуется 50–100) с полями:

| Колонка | Источник |
|---------|----------|
| ID | `task_queue.id` |
| Тип | `task_types.code` / name |
| Статус | `status` |
| Приоритет | `priority` |
| Аккаунт | `account_id` / session_name |
| Попытки | `attempt_count`, `postpone_count` |
| Создана | `created_at` |
| Запуск не раньше | `run_after` |
| Последняя ошибка | `last_error`, `last_error_code` |
| Канал / payload | краткий preview из `payload` |

#### 4.3.1. Phase 1: колонки при источнике `GET /parser/actions`

Источник строк — **bulk-операции clump** (`ActionItemResponse`), не PG `task_queue`. Подзаголовок таблицы: «Bulk-операции (in-memory)». PG-задача по числовому id — отдельно через `?taskId=` и modal D10.

| Колонка (§4.3) | Phase 1 | Поле API / примечание |
|----------------|---------|------------------------|
| ID | **Показать** | `id` (hex string). Заголовок колонки: **«ID операции»** |
| Тип | **Показать** | `action_type` → лейбл: `add_channels` → «Добавление каналов», `remove_channels` → «Удаление каналов» |
| Статус | **Показать** | `status`: `queued`, `running`, `done`, `failed` (§12.8 — отдельные лейблы для actions) |
| Приоритет | **Скрыть** | нет в actions |
| Аккаунт | **Скрыть** | нет в actions |
| Попытки | **Заменить** → колонка **«Прогресс»** | `progress.done` / `progress.total` (каналов); если `total=0` — «—» |
| Создана | **Показать** | `created_at` |
| Запуск не раньше | **Скрыть** | только PG |
| Последняя ошибка | **Показать** | `error` (одна строка; без `last_error_code`) |
| Канал / payload | **Показать** | `{n} каналов: @a, @b…` из `payload.channel_list` (max 2 в ячейке) |
| *(доп.)* Clump | **Показать** | `parser_id` (укороченный, tooltip полный) |

**Скрытые фильтры Phase 1:** аккаунт, «только отложенные».  
**Фильтры Phase 1:** статус, `action_type`, «только с ошибкой» (`failed` или `error != null`), `parser_id`.

**Modal операции (action):** все поля строки + полный `payload.channel_list` + `started_at` / `finished_at`. Блок «PG-задачи»: текст «Список task_ids доступен только в ответе POST add/remove-channels, не в GET actions»; если в URL есть `?taskId=` — показать карточку из `GET /queue/tasks/{id}`.

**Phase 2** (`GET /queue/tasks`): полный набор колонок §4.3; фильтры включая аккаунт и «только отложенные».

Действия (**MVP — только просмотр**, §10.4):

- просмотр детали PG-задачи — `GET /discovery-api/parser/queue/tasks/{id}` (§9.5.3, §9.15);
- просмотр истории попыток — **Phase 2** (§11.6); MVP: `attempt_count`, `postpone_count` в modal.

### 4.4. Блок «Отложенные и проблемные»

- количество **отложенных** (`scheduled` + `postpone_count >= 10`);
- **самая старая задача** — из `queue.oldest_queued_age_seconds` + TBI list;
- алерты — **§11.3** (правила, пороги, тексты UI).


### 4.6. API и контракт данных для вкладки «Очередь»

См. **§9.5** (метрики G3, задача D10, коды ошибок, типы задач) и **§9.15** (эндпойнты `actions`, `queue/tasks`, `queue/metrics`).

### 4.5. Связь с основным ТЗ

- §15.2 — сортировка dispatch: priority DESC, created_at ASC (отображать в подсказке, не менять из UI на MVP).
- §13.4 — stuck по `task_timeout_seconds`.
- §26.5 — проблема **видима** в UI, не только в логах.

---

## 5. Вкладка «Аккаунты»

### 5.1. Назначение

Управление и мониторинг **Telegram-аккаунтов**, участвующих в балансировщике (§6) и в clump discovery (§5.2, [`admin-backend-roadmap.md`](../standalone_discovery/docs/admin-backend-roadmap.md)).

### 5.2. Сводные показатели (§26.3)

| Поле UI | Поле API (`GET …/queue/metrics`) | Описание |
|---------|------------|----------|
| Использовано за час | `accounts.per_op[].used_last_hour` (сумма) | Суммарный расход ресурса |
| Свободный ресурс, % | `accounts.worst_by_account[].worst_available_percent` | По аккаунту (агрегат или худший op) |
| В cooldown | `accounts.in_cooldown` | Количество |
| Активных | `accounts.active` | Доступны для dispatch |
| Без ресурса | `accounts.without_resource` | Не могут принять задачу |
| — | `account_status` | active / cooldown / disabled / banned / error (§6.2) |
| — | `account_error_rate` | Доля ошибок за окно |

### 5.3. Таблица аккаунтов

Для каждого аккаунта (`GET /parser/accounts/all` + PG `accounts`):

| Колонка | Описание |
|---------|----------|
| Имя сессии | `session_name` |
| Отображаемое имя | `display_name` |
| Статус dispatch | `queue_status` (PG: `active` / `cooldown` / …) |
| Статус runtime | `status` (clump: `healthy`, `flood_wait`, `offline`, …) |
| Освободится | `available_at` (ISO UTC) или countdown `available_in_seconds` |
| FloodWait PG | `cooldown_until`, `cooldown_remaining_seconds` |
| FloodWait runtime | `flood_until`, `flood_remaining_seconds` |
| В clump | `in_clump`, `parser_id`, `clump_name` |
| Каналов | `channel_count` / `max_channels` |
| Per-op ресурс | остаток RPH по каждому **op** (из `accounts.per_op[]` в `/queue/metrics`, §0.5) |
| % ресурса | `available_resource_percent` (сводка или min по ops) |
| Последняя ошибка | `last_error`, `last_error_at` (PG; код `flood_wait` и др.) |
| Занят задачей | `current_task_id` (PG, не null = in_progress на аккаунте) |
| Admin block | `admin_blocked`, `block_reason` |
| Действия | block/unblock, enroll в clump (если не в clump) |

**Per-op ресурс:** в таблице — раскрываемая строка или badge «3/5 ops exhausted» с детализацией по кодам op (`JoinChannel`, `get_entity`, …). Это **отображение** backend per-op модели; оператор видит, **какой лимит исчерпан**.

#### 5.3.1. Overlay PG cooldown — входящие данные API

С **2026-06** эндпойнты `/parser/accounts/all`, `/parser/accounts`, `/parser/account-detail` возвращают **overlay-поля** — merge PostgreSQL dispatch и runtime clump. Полная спецификация backend: [`account-cooldown-overlay-api.md`](account-cooldown-overlay-api.md).

**Зачем:** после FloodWait dispatch пишет `accounts.cooldown_until` в PG; clump параллельно держит `flood_until` in-memory. UI получает **одно** поле «когда снова доступен» — `available_at`.

**Требования backend:** `USE_PG_QUEUE=true`. Без PG overlay PG-слоя = `null` (runtime flood может быть заполнен).

| Поле API | Тип | Источник | Колонка / элемент UI | Когда `null` |
|----------|-----|----------|----------------------|--------------|
| `queue_status` | string | PG `accounts.status` | Бейдж **«Dispatch»** | аккаунт не в PG |
| `cooldown_until` | string (ISO UTC, `Z`) | PG | Tooltip «PG cooldown до …» | cooldown истёк |
| `cooldown_remaining_seconds` | int | вычисление | Подпись под бейджем (опц.) | cooldown не активен |
| `available_at` | string (ISO UTC) | `max(PG, runtime)` | Колонка **«Освободится»** | доступен сейчас |
| `available_in_seconds` | int | вычисление | Live countdown / progress | доступен сейчас |
| `flood_until` | float (unix sec) | runtime clump | Tooltip «Runtime flood» | нет in-memory flood |
| `flood_remaining_seconds` | int | runtime clump | Колонка «FloodWait runtime» | нет flood |
| `current_task_id` | int | PG | Индикатор «В работе» + ссылка на задачу | не занят |
| `last_error` | string | PG (приоритет) | Колонка «Последняя ошибка» | нет ошибки |
| `last_error_at` | string (ISO UTC) | PG | Tooltip к ошибке | нет ошибки |
| `is_enabled` | bool | PG | Серый row / «Выключен» | не в PG |
| `generated_at` | string (ISO UTC) | сервер | «Обновлено: …» (только `/accounts/all`, корень) | — |

**Не путать:**

| Поле | Слой | Примеры | Использовать в UI для dispatch? |
|------|------|---------|--------------------------------|
| `status` | Runtime clump | `healthy`, `flood_wait`, `offline` | **Нет** — только колонка «Runtime» |
| `queue_status` | PG dispatch | `active`, `cooldown`, `disabled`, `banned`, `error` | **Да** — бейдж dispatch, §16 ТЗ |

**Правила отображения:**

1. **Колонка «Освободится»:** если `available_in_seconds > 0` — countdown `MM:SS` или «через N мин»; иначе «—» или «сейчас».
2. **Бейдж cooldown:** `queue_status === "cooldown"` **или** `available_in_seconds > 0` (жёлтый/оранжевый).
3. **Countdown:** старт от `available_in_seconds` + якорь `generated_at` (список) или локальный tick каждую секунду; полный refresh списка — каждые 30 с (§12.3).
4. **После рестарта discovery-api:** `flood_until` может быть `null`, но `cooldown_until` / `available_at` из PG — **показывать таймер по PG**.
5. **`accounts.in_cooldown`** из `/queue/metrics` — только **число** в KPI; per-account время **только** из `/accounts/all`.
6. **`admin_blocked`** — отдельно от `queue_status`; блок админа не заменяет PG cooldown.

**Сценарии (acceptance для UI):**

| Ситуация | `queue_status` | `available_at` | Ожидание UI |
|----------|----------------|----------------|-------------|
| Свободен | `active` | `null` | зелёный dispatch, «—» в «Освободится» |
| FloodWait PG (после рестарта API) | `cooldown` | = `cooldown_until` | таймер, бейдж «Cooldown» |
| FloodWait только runtime | `active` / `null` | из `flood_until` | таймер, runtime-бейдж |
| PG cooldown длиннее runtime | `cooldown` | = PG (позже) | показывать PG-время |
| Занят задачей | `active` | `null` | бейдж «В работе», `current_task_id` → modal задачи |
| Banned | `banned` | `null` | красный бейдж, без таймера |
| PG выключен | overlay `null` | runtime only | только `status` / `flood_remaining_seconds` |

**Пример фрагмента ответа** (`GET /parser/accounts/all`):

```json
{
  "total": 2,
  "generated_at": "2026-06-30T00:01:55Z",
  "accounts": [
    {
      "session_name": "Client1",
      "status": "flood_wait",
      "flood_until": 1719701755.0,
      "flood_remaining_seconds": 240,
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

### 5.4. QR-авторизация Telegram-аккаунта

Блок «Добавить аккаунт»:

1. Поле **имя сессии** (`session_name`, `A-Za-z0-9_-`, 1–64).
2. Кнопка **«Создать QR»** → `POST /discovery-api/auth/qr`.
3. Отображение **QR-кода** (`qr_url`) и статуса.
4. Автопolling **статуса** → `GET /discovery-api/auth/qr/{session_id}/status` до `success` / timeout / error.
5. При успехе: `phone`, `user_name`, путь к `session_file`.
6. Опционально: **«Зачислить в clump»** → `POST /parser/{parser_id}/enroll-session` (ручное подключение, admin-backend-roadmap).

Статусы UI: `pending` → `waiting_scan` → `success` | `expired` | `error`.

### 5.5. Ручное управление (§6)

- **Выключить аккаунт** — `PATCH …/accounts/{session_name}/block` или PG `is_enabled=false` / `status=disabled`.
- **Разблокировать** — обратная операция.
- Просмотр **cooldown_until** / flood remaining: поля overlay в `GET /parser/accounts/all` — см. [`docs/account-cooldown-overlay-api.md`](account-cooldown-overlay-api.md).


### 5.7. API для вкладки «Аккаунты»

| Действие UI | Метод | Path |
|-------------|-------|------|
| Список аккаунтов | GET | `/discovery-api/parser/accounts/all` |
| Per-op RPH | GET | `/discovery-api/parser/queue/metrics` → `accounts.per_op[]` |
| Деталь / каналы | GET | `/discovery-api/parser/account-detail`, `…/account-channels` |
| QR | POST/GET/DELETE | `/discovery-api/auth/qr`, `…/qr/{id}/status`, `…/qr/{id}` |
| Block / meta | PATCH | `/discovery-api/parser/accounts/{session}/block`, `…/accounts/{session}` |
| Enroll в clump | POST | `/discovery-api/parser/{parser_id}/enroll-session` |

Полные спецификации — **§9.9**, **§9.14**.

### 5.6. Связь с основным ТЗ

- §6.2 — статусы `active`, `cooldown`, `disabled`, `banned`, `error`.
- §16 — dispatch не берёт disabled/cooldown/banned аккаунты.
- §26.4 — алерт при массовых ошибках одного аккаунта или исчерпании ресурса у всех.

---

## 6. Вкладка «RPH»

### 6.1. Назначение

Ручная настройка **лимитов пропускной способности (requests per hour, RPH)** для **типов задач балансировщика** — единиц работы, которые оператор понимает как «вызов API / тип задачи в очереди», а **не** как отдельные Telethon RPC (JoinChannel, SearchGlobal и т.д.).

> **Уточнение относительно backend:** в утверждённой схеме PG лимиты хранятся в `resource_op_types.rph_limit` (per Telethon op, §0.5 [`итогового плана`](../docs(plan)/итоговый-план-разработки.md)). Вкладка «RPH» работает на **уровне абстракции UI = `task_types.code`** (§8.1). Backend либо хранит отображаемый RPH на `task_types`, либо вычисляет/проксирует его из связанных op. Оператор **не редактирует** `resource_op_types` напрямую.

### 6.2. Список настраиваемых типов задач

Таблица по `task_types` (§8.1), минимум MVP-типы:

| code | Название (пример) |
|------|-------------------|
| `parser_add_channel` | Добавление канала в парсер |
| `parser_remove_channel` | Удаление канала |
| `move_channel` | Перенос канала между аккаунтами |
| `collect_extra_data` | Сбор доп. данных по каналу |
| `update_channel` | Обновление метаданных канала |

### 6.3. Редактируемые поля (на строку типа задачи)

| Поле | Связь с ТЗ §8.1 | Описание |
|------|-----------------|----------|
| **RPH (лимит/час)** | UI-уровень; аналог «сколько таких задач в час может пройти через аккаунт» | Число ≥ 1; сохраняется в конфиг типа задачи |
| `is_enabled` | §8.1 | Включён ли тип в dispatch и продюсерах |
| `default_priority` | §8.2 | Приоритет по умолчанию |
| `min_available_resource_percent` | §8.1 | Мин. % свободного ресурса для запуска |
| `target_queue_size` | §8.3, §22 | Целевой размер очереди для продюсера |
| `max_attempts` | §8.1 | Макс. реальных попыток |
| `retry_delay_seconds`, `retry_backoff_multiplier`, `max_retry_delay_seconds` | §20.2 | Retry-политика |
| `max_postpone_count` | §9.3, §26.4 | Порог алерта по отложениям |
| `task_timeout_seconds` | §13.4 | Таймаут → stuck |

Read-only в UI:

- текущее **использование RPH** за последний час по типу (из метрик/usage);
- количество задач в очереди этого типа.

### 6.4. Поведение при сохранении

- Валидация: RPH > 0; priority — целое; проценты 0–100.
- Изменения пишутся в `task_types` (+ при необходимости синхронизация с `resource_op_types` на backend).
- **Audit (желательно):** кто, когда, старое/новое значение RPH (G6★ предполагает rollback лимитов).
- G6★ (авто-снижение RPH при повторяющихся ошибках) — отображать флаг «автоматически снижен» и кнопку «Вернуть дефолт» (admin override).


### 6.6. API для вкладки «RPH»

| Действие UI | Метод | Path | Статус |
|-------------|-------|------|--------|
| Факт usage per op | GET | `/discovery-api/parser/queue/metrics` | prod |
| Глобальные дефолты | GET | `/discovery-api/parser/settings` | prod |
| Конфиг clump | GET/PATCH | `/discovery-api/parser/{parser_id}/config` | prod |
| CRUD task_types | GET/PATCH | `/discovery-api/parser/queue/task-types/{code}` | TBI (§9.4) |

Per-op лимиты — **§9.5.5**. Полные спецификации — **§9.13**, **§9.15**.

### 6.5. Связь с основным ТЗ

- §8 — все лимиты и приоритеты **не зашиты в код**, управляются через конфиг.
- §22.3 — продюсер `channel_balancer` учитывает `min_available_resource_percent` и `target_queue_size`.
- §23–§24 — `collect_extra_data`, `update_channel` — те же правила.
- §7.4 — по умолчанию 1 задача = 1 единица ресурса (UI RPH = «задач в час», не «RPC в час»).

---

## 7. Нефункциональные требования

| ID | Требование |
|----|------------|
| NF-1 | Время загрузки вкладки «Очередь» ≤ 3 с при очереди до 10k задач (пагинация таблицы) |
| NF-2 | Метрики консистентны с `GET /queue/metrics` (единый снимок `generated_at`) |
| NF-3 | QR-flow не хранит секреты в localStorage дольше сессии |
| NF-4 | Ошибки API — toast/баннер на русском |
| NF-5 | Mobile: адаптив минимум для таблиц (горизонтальный scroll) |

---

## 8. Критерии приёмки

1. В дашборде есть вкладка **«Балансировщик»** с тремя подвкладками: **Очередь**, **Аккаунты**, **RPH**.
2. **Очередь** показывает все метрики §26.2, таблицу последних задач, oldest task, postponed/stuck counts, `done_last_5_min`.
3. **Аккаунты** показывает метрики §26.3, список аккаунтов с per-op остатком ресурса, overlay cooldown (`available_at`, `queue_status`, §5.3.1) и ошибками; работает QR-авторизация и polling статуса.
4. **RPH:** Phase 1 — read-only (counts, settings, clump config); Phase 2 — редактирование `task_types` без прямого редактирования Telethon op (§10.1, §10.6).
5. При `USE_PG_QUEUE=false` вкладка показывает понятное сообщение, а не пустой экран.
6. Алерты §26.4 отображаются в блоке «Проблемы» (хотя бы `high_postpone`, queue growth, no active accounts).
7. Данные совпадают с prod-запросами `GET …/queue/metrics` и `GET …/accounts/all` при том же `generated_at` (§9.5).

---

## 9. Backend API (Discovery API)

Prod-сервер: `https://lidogen-balancer-tg-prod.web.oboyma.ai`

Источник истины на prod: `GET https://lidogen-balancer-tg-prod.web.oboyma.ai/openapi.json`

### 9.1. Базовые параметры и аутентификация

| Параметр | Значение |
|----------|----------|
| **Base URL (prod)** | `https://lidogen-balancer-tg-prod.web.oboyma.ai` |
| **Health (без ключа)** | `GET https://lidogen-balancer-tg-prod.web.oboyma.ai/health` |
| **Swagger UI** | `https://lidogen-balancer-tg-prod.web.oboyma.ai/docs` |
| **OpenAPI** | `https://lidogen-balancer-tg-prod.web.oboyma.ai/openapi.json` |
| **Локально (VM)** | `http://127.0.0.1:8100` |
| **Аутентификация** | `X-API-Key: <API_KEY>` на всех `/discovery-api/**` |
| **Формат** | `Content-Type: application/json` |

Ключ prod берётся из секретов деплоя (не из локального `.env`).

| HTTP-код | Когда |
|----------|-------|
| `401` | `X-API-Key` отсутствует или неверен |
| `400` | Ошибка валидации тела/query |
| `404` | Clump, аккаунт, задача или QR-сессия не найдены |
| `409` | Clump остановлен, квота каналов, нельзя удалить аккаунт |
| `502` | Ошибка отправки ботом |
| `503` | PG-очередь выключена или `API_KEY` не задан на сервере |

```powershell
$BASE = "https://lidogen-balancer-tg-prod.web.oboyma.ai"
$KEY  = "<PROD_API_KEY>"
curl.exe -sS -H "X-API-Key: $KEY" "$BASE/discovery-api/parser/queue/metrics"
```

```bash
BASE=https://lidogen-balancer-tg-prod.web.oboyma.ai
KEY=ВАШ_API_KEY
```

### 9.2. Рекомендация BFF

Для фронта дашборда — серверный прокси (`/api/admin/balancer/*`), который вызывает discovery-api с `X-API-Key` на бэкенде платформы (ключ не в браузере).

### 9.3. Сводная таблица: UI → API

| UI-раздел | Операция | Метод | Path |
|-----------|----------|-------|------|
| Общее | Health-check | GET | `/health` |
| Очередь | KPI, алерты, per-op | GET | `/discovery-api/parser/queue/metrics` |
| Очередь | Список action-задач | GET | `/discovery-api/parser/actions` |
| Очередь | Деталь action | GET | `/discovery-api/parser/actions/{action_id}` |
| Очередь | Деталь PG-задачи | GET | `/discovery-api/parser/queue/tasks/{task_id}` |
| Очередь | Список / статус clump | GET | `/discovery-api/parser/list`, `…/status/{parser_id}` |
| Очередь | Каналы clump | GET | `/discovery-api/parser/{parser_id}/channels` |
| Очередь | Поставить add/remove | POST | `/discovery-api/parser/{parser_id}/add-channels`, `…/remove-channels` |
| Очередь | Запуск / остановка clump | POST, DELETE | `/discovery-api/parser/start`, `…/stop/{id}`, DELETE `…/{id}` |
| Аккаунты | Список | GET | `/discovery-api/parser/accounts/all` |
| Аккаунты | Деталь / каналы | GET | `/discovery-api/parser/account-detail`, `…/account-channels` |
| Аккаунты | QR | POST, GET, DELETE | `/discovery-api/auth/qr`, `…/qr/{id}/status`, `…/qr/{id}` |
| Аккаунты | Block / meta / delete | PATCH, DELETE | `/discovery-api/parser/accounts/{session}/block`, `…/accounts/{session}` |
| Аккаунты | Enroll / sessions | POST | `/discovery-api/parser/{parser_id}/enroll-session`, `…/add-session`, `…/remove-session` |
| RPH | Глобальные дефолты | GET | `/discovery-api/parser/settings` |
| RPH | Конфиг clump | GET, PATCH | `/discovery-api/parser/{parser_id}/config` |
| RPH | Факт usage | GET | `/discovery-api/parser/queue/metrics` → `accounts.per_op` |
| RPH | CRUD task_types | — | **TBI** (§9.4) |

### 9.4. Эндпойнты TBI (ещё не на prod)

Полные контракты — **§14**.

| Метод | Path | Назначение |
|-------|------|------------|
| GET | `/discovery-api/parser/queue/tasks` | Список PG-задач с фильтрами |
| GET | `/discovery-api/parser/queue/task-types` | Список `task_types` |
| PATCH | `/discovery-api/parser/queue/task-types/{code}` | Изменение RPH, retry, priority |
| GET | `/discovery-api/parser/queue/tasks/{id}/attempts` | История попыток |
| GET/PATCH | `/discovery-api/parser/queue/resource-op-types/{op_code}` | Override per op (скрыто в UI MVP) |

Fallback до TBI — **§10.5–10.6**.

### 9.5. Контракт PG-очереди (метрики, задачи, ошибки)

#### 9.5.1. GET /discovery-api/parser/queue/metrics (G3)

**Prod URL:** `https://lidogen-balancer-tg-prod.web.oboyma.ai/discovery-api/parser/queue/metrics`

Требует `USE_PG_QUEUE=true` на сервере. Ошибка `503` — очередь выключена или БД недоступна.

**Пример ответа:**

```json
{
  "queue": {
    "total": 42,
    "by_status": {"queued": 10, "in_progress": 2, "retry": 1},
    "by_type": {"parser_add_channel": {"queued": 10}},
    "oldest_queued_age_seconds": 120,
    "stuck_count": 0,
    "done_last_5_min": 10
  },
  "accounts": {
    "active": 5,
    "in_cooldown": 1,
    "without_resource": 2,
    "per_op": [
      {
        "account_id": 1,
        "session_name": "acc1",
        "account_status": "active",
        "op_type_id": 10,
        "op_code": "get_entity",
        "effective_rph": 6,
        "used_last_hour": 2,
        "available_resource": 4,
        "available_resource_percent": 66.67
      }
    ],
    "worst_by_account": [
      {
        "account_id": 1,
        "session_name": "acc1",
        "account_status": "active",
        "worst_available_percent": 66.67,
        "any_op_exhausted": false,
        "exhausted_ops_count": 0
      }
    ]
  },
  "alerts_preview": {"high_postpone_count": 3},
  "generated_at": "2026-06-25T12:00:00+00:00"
}
```

#### 9.5.2. Маппинг полей UI ↔ API (§4, §5)

| Поле UI (§4.2) | Поле API `queue/metrics` |
|----------------|---------------------------|
| Всего в очереди | `queue.total` |
| По статусам | `queue.by_status` |
| По типам | `queue.by_type` |
| Зависшие | `queue.stuck_count` |
| Выполнено за 5 мин | `queue.done_last_5_min` |
| Возраст старейшей | `queue.oldest_queued_age_seconds` |
| Активных аккаунтов | `accounts.active` |
| В cooldown | `accounts.in_cooldown` |
| Без ресурса | `accounts.without_resource` |
| Per-op ресурс | `accounts.per_op[]` |
| Часто отложенные (алерт) | `alerts_preview.high_postpone_count` |

#### 9.5.2.1. Маппинг overlay аккаунтов (§5.3, §5.3.1)

Источник: `GET /discovery-api/parser/accounts/all` (и `/accounts`, `/account-detail`).

| Поле UI (§5.3) | Поле API | Примечание |
|----------------|----------|------------|
| Статус dispatch | `queue_status` | PG; не путать с runtime `status` |
| Статус runtime | `status` | clump / listener |
| Освободится | `available_at`, `available_in_seconds` | главное для FloodWait UI |
| FloodWait PG | `cooldown_until`, `cooldown_remaining_seconds` | tooltip / expand |
| FloodWait runtime | `flood_until`, `flood_remaining_seconds` | tooltip / expand |
| Занят задачей | `current_task_id` | link → `?tab=queue&taskId=` |
| Последняя ошибка | `last_error`, `last_error_at` | PG приоритетнее runtime |
| Admin block | `admin_blocked`, `block_reason` | отдельно от `queue_status` |
| Включён в dispatch | `is_enabled` | серый row если `false` |
| Якорь снимка | `generated_at` | корень `/accounts/all` only |

KPI «В cooldown» (`accounts.in_cooldown`) — агрегат из `/queue/metrics`; **не** заменяет per-account overlay.

Статусы в `by_status`: `queued`, `scheduled`, `in_progress`, `retry`, `failed`, `stuck`, `done`, `cancelled` (точный набор — из БД).

#### 9.5.3. GET /discovery-api/parser/queue/tasks/{task_id} (D10)

**Prod URL:** `https://lidogen-balancer-tg-prod.web.oboyma.ai/discovery-api/parser/queue/tasks/{task_id}`

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | int | ID в `task_queue` |
| `task_type_code` | string | Код типа (`parser_add_channel`, `move_channel`, …) |
| `status` | string | Статус задачи |
| `attempt_count` | int | Число реальных попыток |
| `postpone_count` | int | Число отложений без attempt |
| `last_error` | string\|null | Полный код/текст (`insufficient_resource:42:get_entity`) |
| `last_error_code` | string\|null | Машиночитаемый префикс без суффикса — **для алертов** |
| `payload` | object | Данные задачи (канал, parser_id, …) |
| `run_after` | string\|null | ISO — не запускать раньше |
| `started_at`, `finished_at`, `last_error_at` | string\|null | ISO-времена |

#### 9.5.4. Коды ошибок задач (для колонки «Последняя ошибка»)

#### Retryable (повтор через `run_after`)

| `last_error_code` | Причина | Действие оператора |
|-------------------|---------|-------------------|
| `flood_wait` | FloodWait от Telegram | Подождать; при частоте — снизить RPH op |
| `clump_error` | Ошибка clump | Проверить логи clump |
| `clump_not_loaded` | Parser clump не загружен | Запустить парсер (`POST /parser/start`) |
| `transient_error` | Timeout / сеть | Обычно проходит сам |
| `unknown_task_type:*` | Тип не найден или выключен | Проверить `task_types` |

#### Permanent (terminal `failed`)

| `last_error_code` | Причина |
|-------------------|---------|
| `invalid_payload` | Невалидный payload |
| `account_not_found` | Аккаунт отсутствует в PG |
| `unsupported_task_type` | Adapter не поддерживает тип |

#### Postpone (отложить без attempt)

| `last_error_code` | Причина |
|-------------------|---------|
| `insufficient_resource:*` | RPH op исчерпан (суффикс — account и op) |
| `missing_availability` | Нет данных availability для op |
| `no_available_account` | Нет свободного аккаунта |
| `no_ops_for_role:*` | Нет enabled op для роли |
| `account_reserve_failed:*` | Не удалось зарезервировать аккаунт |
| `dual_account_reserve_failed:*` | Не удалось зарезервировать пару |
| `missing_dual_accounts` | Нет source/target в dual-задаче |
| `dual_accounts_same_id` | source == target |

#### Системные

| `last_error_code` | Причина |
|-------------------|---------|
| `watchdog:task_timeout_exceeded` | Задача зависла в `in_progress` → `stuck` |
| `unexpected_error` | Нетипизированное исключение |

#### 9.5.5. Per-op RPH (§0.5)

Лимиты учитываются **по типу Telethon op**, не по аккаунту целиком:

`effective_rph = floor(rph_limit × (1 − reserve_percent/100))`, `reserve_percent = 10`.

| op_code | rph_limit | effective_rph |
|---------|-----------|---------------|
| `get_entity` | 7 | 6 |
| `channels.JoinChannel` | 30 | 27 |
| `channels.GetFullChannel` | 80 | 72 |
| `channels.GetParticipants` | 500 | 450 |
| `iter_messages` | 450 | 405 |
| `channels.LeaveChannel` | 30 | 27 |

В UI отображать `accounts.per_op[]` из `queue/metrics`: `op_code`, `used_last_hour`, `effective_rph`, `available_resource_percent`.

#### 9.5.6. HTTP-коды ошибок API (общие)

| Код | Когда |
|-----|-------|
| `401` | `X-API-Key` отсутствует или неверен |
| `400` | Ошибка валидации тела/query |
| `404` | Clump, аккаунт, задача или QR-сессия не найдены |
| `409` | Конфликт: clump остановлен, квота каналов, нельзя удалить аккаунт |
| `502` | Ошибка отправки ботом |
| `503` | PG-очередь выключена (`USE_PG_QUEUE=false`) или `API_KEY` не задан на сервере |

#### 9.5.7. Типы задач PG-очереди (MVP)

| `task_type_code` | Назначение |
|------------------|------------|
| `parser_add_channel` | Добавление канала в парсер |
| `parser_remove_channel` | Удаление канала |
| `move_channel` | Перенос канала между аккаунтами |
| `collect_extra_data` | Сбор доп. данных по каналу |
| `update_channel` | Обновление метаданных канала |

Постановка задач add/remove — через `POST …/add-channels` и `POST …/remove-channels` (async); в ответе поле `task_ids[]`.

#### 9.5.8. Overlay PG cooldown в ответах `/accounts/*`

Реализовано в discovery-api (merge PG + runtime). Детали — **§5.3.1**, **§11.13**, [`account-cooldown-overlay-api.md`](account-cooldown-overlay-api.md).

| Эндпойнт | Overlay | `generated_at` |
|----------|---------|----------------|
| `GET /parser/accounts/all` | да | да (корень) |
| `GET /parser/accounts` | да | нет |
| `GET /parser/account-detail` | да (верхний уровень + `health`) | нет |

Overlay-поля (`AccountQueueOverlayFields`): `queue_status`, `cooldown_until`, `cooldown_remaining_seconds`, `available_at`, `available_in_seconds`, `flood_until`, `current_task_id`, `last_error`, `last_error_at`, `is_enabled`.

Формула: `available_at = max(cooldown_until, datetime(flood_until))` при обоих > now().

---

### 9.7. Системные эндпойнты

#### GET /health

Проверка живости сервиса. **Без** API-ключа.

- Вход: нет.
- Выход: `{"status": "в порядке"}`

```bash
curl -sS "$BASE/health"
```

---

### 9.8. Discovery — поиск каналов (вне scope UI, справочно)

Префикс: `/discovery-api`. Требуется `X-API-Key`.

#### POST /discovery-api/discover

Поиск broadcast-каналов (опционально групп) по запросу с обходом «похожих» в глубину.

**Тело запроса** (`DiscoveryRequest`):

| Поле | Тип | Обяз. | По умолч. | Описание |
|------|-----|-------|-----------|----------|
| `session_name` | string | да | — | Имя/путь Telethon `.session` на сервере (без расширения) |
| `query` | string | да | — | Поисковый запрос |
| `first_pass_limit` | int (1–100) | нет | 10 | Лимит результатов первого прохода |
| `similarity_depth` | int (0–5) | нет | 2 | Глубина обхода похожих каналов |
| `include_global_search` | bool | нет | true | Доп. поиск по тексту сообщений (`messages.SearchGlobal`) |
| `include_groups` | bool | нет | false | Возвращать также группы/супергруппы |

**Ответ** (`DiscoveryResponse`):

| Поле | Тип | Описание |
|------|-----|----------|
| `query` | string | Эхо запроса |
| `total` | int | Кол-во найденных каналов |
| `depth_stats` | object<int,int> | Кол-во каналов по глубине |
| `channels` | array<ChannelItem> | Список каналов (см. ниже) |

`ChannelItem` (ключевые поля): `peer_id` (int), `title` (string), `username` (string|null),
`participants_count` (int|null), `depth` (int), `source` (string), `recommended_by` (int|null),
`score` (int), `score_breakdown` (object), `score_signals` (object), `score_hard_flags` (object),
плюс флаги Telegram: `verified`, `scam`, `fake`, `megagroup`, `broadcast`, `about`, `created_at` и др.

```bash
curl -sS -X POST "$BASE/discovery-api/discover" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{
    "session_name": "my_account",
    "query": "крипта трейдинг",
    "first_pass_limit": 10,
    "similarity_depth": 2,
    "include_global_search": true,
    "include_groups": false
  }'
```

#### POST /discovery-api/discover-groups

Поиск групп/супергрупп по слову. **Ошибки не бросают 500** — возвращаются в поле `errors`.

**Тело запроса** (`GroupDiscoveryRequest`):

| Поле | Тип | Обяз. | По умолч. | Описание |
|------|-----|-------|-----------|----------|
| `session_name` | string | да | — | Имя/путь Telethon `.session` |
| `word` | string | да | — | Поисковое слово |
| `limit` | int (1–100) | нет | 20 | Лимит результатов |
| `depth` | int (0–5) | нет | 2 | Глубина обхода |

**Ответ** (`GroupDiscoveryResponse`): `query`, `seeds` (array<string>), `total`, `depth_stats`,
`groups` (array<GroupItem>), `errors` (array<string>).

```bash
curl -sS -X POST "$BASE/discovery-api/discover-groups" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"my_account","word":"маркетинг","limit":20,"depth":2}'
```

#### POST /discovery-api/add-channel-by-link

Резолв и добавление одного канала/чата по ссылке через указанную сессию.

**Тело** (`AddChannelByLinkRequest`): `session_name` (string, обяз.), `link` (string, обяз.).
**Ответ:** `ChannelItem`.
**Ошибки:** `400` (нет обсуждения / нет доступа / некорректная ссылка), `500` (прочее).

```bash
curl -sS -X POST "$BASE/discovery-api/add-channel-by-link" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"my_account","link":"https://t.me/example_channel"}'
```

#### POST /discovery-api/add-channel-by-link-session-file

То же, но сессия задаётся полем `session_file` (путь/имя `.session`).

**Тело** (`AddChannelByLinkSessionFileRequest`): `session_file` (string, обяз.), `link` (string, обяз.).
**Ответ:** `ChannelItem`.

```bash
curl -sS -X POST "$BASE/discovery-api/add-channel-by-link-session-file" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_file":"my_account","link":"https://t.me/example_channel"}'
```

---

### 9.9. QR-авторизация Telegram-аккаунта

#### POST /discovery-api/auth/qr

Создаёт QR-сессию для входа в Telegram. Лимит rate (nginx): 10 запросов/мин с IP.

**Тело** (`QRCreateRequest`, можно пустое `{}`):

| Поле | Тип | Описание |
|------|-----|----------|
| `session_name` | string\|null | Имя файла сессии для автосохранения (`<SESSIONS_DIR>/<name>.session`). Символы `A-Za-z0-9_-`, длина 1–64. Если не задан — сохранять `session_string` вручную |

**Ответ** (`QRCreateResponse`): `session_id`, `qr_url`, `status`, `session_name`.

```bash
curl -sS -X POST "$BASE/discovery-api/auth/qr" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"new_account"}'
```

#### GET /discovery-api/auth/qr/{session_id}/status

Статус QR-сессии. Поллить до `status=="success"`.

**Path:** `session_id`.
**Ответ** (`QRStatusResponse`): `session_id`, `status`, `qr_url`, и при успехе — `phone`,
`user_id`, `user_name`, `session_string`, `session_file`, `session_file_error`.
**Ошибки:** `404` — сессия не найдена/истекла.

```bash
curl -sS "$BASE/discovery-api/auth/qr/SESSION_ID/status" -H "X-API-Key: $KEY"
```

#### DELETE /discovery-api/auth/qr/{session_id}

Удаляет/освобождает QR-сессию. **Ответ:** `{"ok": true}`.

```bash
curl -sS -X DELETE "$BASE/discovery-api/auth/qr/SESSION_ID" -H "X-API-Key: $KEY"
```

---

### 9.10. Бот — отправка сообщений (вне scope UI, справочно)

#### POST /discovery-api/bot/send-message

Отправляет сообщение через бота (текст/картинка/кнопки).

**Тело** (`BotMessageRequest`):

| Поле | Тип | Обяз. | Описание |
|------|-----|-------|----------|
| `chat_id` | int | да | Telegram chat_id получателя |
| `text` | string\|null | нет | Текст или HTML-caption |
| `image_url` | string\|null | нет | URL изображения |
| `layout` | string | нет | Тип кнопок: `inline` (по умолч.) или `keyboard` |
| `buttons` | array | нет | Описание кнопок |

**Ответ** (`BotMessageResponse`): `ok` (bool), `message_id` (int|null), `chat_id` (int|null).
**Ошибки:** `400` (валидация), `502` (ошибка отправки).

```bash
curl -sS -X POST "$BASE/discovery-api/bot/send-message" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"chat_id":-1001234567890,"text":"Привет","layout":"inline","buttons":[]}'
```

---

### 9.11. Парсер — жизненный цикл clump

Префикс: `/discovery-api/parser`. Требуется `X-API-Key`.

#### POST /discovery-api/parser/start

Создаёт и запускает clump (пул аккаунтов, слушающих каналы) с webhook-доставкой сообщений.

**Тело** (`ParserStartRequest`):

| Поле | Тип | Обяз. | Описание |
|------|-----|-------|----------|
| `session_name` | string\|null | * | Один аккаунт (legacy) |
| `session_name_list` | array<string> | * | Пул аккаунтов для шардирования каналов |
| `clump_name` | string\|null | нет | Имя clump для логов |
| `channel_list` | array<string> | да | `@username`, `t.me/...` или числовые id (мин. 1) |
| `webhook_url` | string (URL) | да | Куда POST-ить JSON при новом сообщении |

\* Нужно указать **ровно одно** из `session_name` / `session_name_list` (иначе `400`).

**Ответ** (`ParserStartResponse`): `parser_id` (string), `assignments` (object<канал,сессия>), `detail`.
**Ошибки:** `400` (валидация/ни один канал не добавлен), `409` (превышена квота каналов), `500` (нет `API_ID`/`API_HASH` или ошибка запуска).

```bash
curl -sS -X POST "$BASE/discovery-api/parser/start" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{
    "session_name_list": ["acc1","acc2"],
    "clump_name": "my_clump",
    "channel_list": ["@channel_a","@channel_b"],
    "webhook_url": "https://your-n8n/webhook/telegram"
  }'
```

#### POST /discovery-api/parser/stop/{parser_id}

Останавливает clump и удаляет его из памяти/хранилища.
**Ответ** (`ParserStopResponse`): `parser_id`, `detail`. **Ошибки:** `404`.

```bash
curl -sS -X POST "$BASE/discovery-api/parser/stop/PARSER_ID" -H "X-API-Key: $KEY"
```

#### DELETE /discovery-api/parser/{parser_id}

Останавливает и удаляет запись clump (аналог stop). **Ответ:** `ParserStopResponse`. **Ошибки:** `404`.

```bash
curl -sS -X DELETE "$BASE/discovery-api/parser/PARSER_ID" -H "X-API-Key: $KEY"
```

#### GET /discovery-api/parser/status/{parser_id}

Статус одного clump. **Ответ:** `ParserStatusItem` (ниже). **Ошибки:** `404`.

```bash
curl -sS "$BASE/discovery-api/parser/status/PARSER_ID" -H "X-API-Key: $KEY"
```

#### GET /discovery-api/parser/list

Список всех активных clump. **Ответ:** `array<ParserStatusItem>`.

`ParserStatusItem`: `parser_id`, `clump_name`, `session_name`, `session_name_list`,
`webhook_url`, `channel_list`, `assignments`, `per_session` (array<object>),
`running` (bool), `finished` (bool), `cancelled` (bool), `error` (string|null),
`started_at` (float, unix), `queue_size` (int), `stats` (object), `health_summary` (object).

```bash
curl -sS "$BASE/discovery-api/parser/list" -H "X-API-Key: $KEY"
```

---

### 9.12. Парсер — управление каналами

#### GET /discovery-api/parser/{parser_id}/channels

Список каналов clump. **Ответ** (`ChannelsListResponse`): `parser_id`, `channel_list` (array<string>),
`allowed_chat_ids` (array<int>), `by_session` (object<сессия, array<string>>). **Ошибки:** `404`.

```bash
curl -sS "$BASE/discovery-api/parser/PARSER_ID/channels" -H "X-API-Key: $KEY"
```

#### POST /discovery-api/parser/{parser_id}/add-channels

Добавляет каналы в clump. По умолчанию **асинхронно** (через очередь).

**Query:** `async` (bool, по умолч. `true`). При `async=true` и включённой PG-очереди задача
ставится в очередь и исполняется воркером.
**Тело** (`ChannelsBody`): `channel_list` (array<string>, мин. 1).
**Ответ** (`AddChannelsResponse`):

| Поле | Тип | Описание |
|------|-----|----------|
| `parser_id` | string | id clump |
| `channel_list` | array<string> | текущий список каналов |
| `added` | array<string> | добавлены (sync) |
| `already_present` | array<string> | уже были (sync) |
| `errors` | array<string> | ошибки (sync) |
| `pending` | array<string> | отложены до HealthMonitor (sync) |
| `assignments` | object | канал→сессия (sync) |
| `action_id` | string\|null | id задачи (async) |
| `task_ids` | array<int> | id задач PG-очереди (async + PG) |
| `async_mode` | bool | режим обработки |

**Ошибки:** `404` (нет clump), `409` (clump остановлен / квота).

```bash
# асинхронно (по умолчанию)
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/add-channels" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"channel_list":["@new_channel","t.me/another"]}'

# синхронно
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/add-channels?async=false" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"channel_list":["@new_channel"]}'
```

#### POST /discovery-api/parser/{parser_id}/remove-channels

Удаляет каналы. Аналогично add-channels: `async` (по умолч. `true`).
**Тело:** `ChannelsBody`.
**Ответ** (`RemoveChannelsResponse`): `parser_id`, `channel_list`, `removed`, `not_found`,
`errors`, `action_id`, `task_ids`, `async_mode`.

```bash
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/remove-channels" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"channel_list":["@old_channel"]}'
```

---

### 9.13. Парсер — конфигурация и глобальные настройки

#### GET /discovery-api/parser/{parser_id}/config

Текущая конфигурация clump. **Ответ** (`ClumpConfigResponse`): `parser_id`, `config` (object). **Ошибки:** `404`.

```bash
curl -sS "$BASE/discovery-api/parser/PARSER_ID/config" -H "X-API-Key: $KEY"
```

#### PATCH /discovery-api/parser/{parser_id}/config

Переопределяет настройки clump (отправляются только изменяемые поля; `null`-поля игнорируются).

**Тело** (`ClumpConfigUpdate`, все поля опциональны):
`max_channels_per_session` (int≥1), `max_reconnects` (int≥1), `reconnect_backoff_base` (float>0),
`reconnect_backoff_max` (float≥1), `flood_migrate_threshold_seconds` (int≥1),
`resolve_min_interval` (float≥0), `auto_migrate` (bool), `add_channels_per_hour` (int≥0),
`rebalance_enabled` (bool), `rebalance_idle_start_hour` (0–23), `rebalance_idle_end_hour` (0–23),
`rebalance_high_watermark_ratio` (0<x≤1), `rebalance_low_watermark_ratio` (0≤x<1),
`rebalance_min_gap_channels` (int≥1), `rebalance_max_moves_per_tick` (int≥1),
`rebalance_cooldown_hours` (float≥0).

**Ответ:** `ClumpConfigResponse`.

```bash
curl -sS -X PATCH "$BASE/discovery-api/parser/PARSER_ID/config" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"max_channels_per_session":300,"auto_migrate":true}'
```

#### GET /discovery-api/parser/settings

Глобальные дефолты балансировщика (из окружения) + описания полей.
**Ответ** (`BalancerSettingsResponse`): `settings` (object), `descriptions` (object<поле, текст>).

```bash
curl -sS "$BASE/discovery-api/parser/settings" -H "X-API-Key: $KEY"
```

---

### 9.14. Парсер — аккаунты (сессии)

#### GET /discovery-api/parser/accounts/all

Все аккаунты (хранилище + clump + overlay PG). **Ответ** (`AccountAllListResponse`):
`total` (int), `accounts` (array<AccountFullSummary>), `generated_at` (ISO UTC).

`AccountFullSummary` — runtime-поля (`session_name`, `display_name`, `status`, `connected`, `running`, `channel_count`, `flood_remaining_seconds`, …) **плюс overlay PG** (§5.3.1):

| Поле | Тип | UI |
|------|-----|-----|
| `queue_status` | string\|null | бейдж dispatch |
| `cooldown_until` | string\|null (ISO UTC) | tooltip PG cooldown |
| `cooldown_remaining_seconds` | int\|null | секунд до PG cooldown |
| `available_at` | string\|null (ISO UTC) | **колонка «Освободится»** |
| `available_in_seconds` | int\|null | live countdown |
| `flood_until` | float\|null (unix) | runtime flood (tooltip) |
| `current_task_id` | int\|null | «В работе» → задача |
| `last_error` | string\|null | код/текст ошибки очереди |
| `last_error_at` | string\|null (ISO UTC) | время ошибки |
| `is_enabled` | bool\|null | выключен из dispatch |

**Пример ответа** (аккаунт в FloodWait):

```json
{
  "total": 1,
  "generated_at": "2026-06-30T00:01:55Z",
  "accounts": [{
    "session_name": "Client1",
    "status": "flood_wait",
    "flood_until": 1719701755.0,
    "flood_remaining_seconds": 240,
    "queue_status": "cooldown",
    "cooldown_until": "2026-06-30T00:15:00Z",
    "available_at": "2026-06-30T00:15:00Z",
    "available_in_seconds": 270,
    "last_error": "flood_wait",
    "is_enabled": true
  }]
}
```

Полная спецификация: [`account-cooldown-overlay-api.md`](account-cooldown-overlay-api.md).

```bash
curl -sS "$BASE/discovery-api/parser/accounts/all" -H "X-API-Key: $KEY"
```

#### GET /discovery-api/parser/accounts

Аккаунты только активных clump. **Ответ** (`AccountListResponse`): `total`, `accounts` (array<AccountSummary>).

Те же overlay-поля, что у `AccountFullSummary` (без `generated_at` на корне).

```bash
curl -sS "$BASE/discovery-api/parser/accounts" -H "X-API-Key: $KEY"
```

#### GET /discovery-api/parser/account-detail

Деталь по аккаунту. **Query:** `session_name` (обяз.), `parser_id` (опц.).
**Ответ:** `AccountDetail` — overlay PG на верхнем уровне + объект `health` (runtime). **Ошибки:** `404`.

```bash
curl -sS "$BASE/discovery-api/parser/account-detail?session_name=acc1&parser_id=PARSER_ID" \
  -H "X-API-Key: $KEY"
```

#### GET /discovery-api/parser/account-channels

Каналы аккаунта. **Query:** `session_name` (обяз.), `parser_id` (опц.).
**Ответ** (`AccountChannelsResponse`): `parser_id`, `session_name`, `channel_count`, `channels`.

```bash
curl -sS "$BASE/discovery-api/parser/account-channels?session_name=acc1" -H "X-API-Key: $KEY"
```

#### PATCH /discovery-api/parser/account-meta

Обновляет метаданные аккаунта (имя/описание/лимит). **Тело** (`AccountMetaUpdate`):
`session_name` (обяз.), `parser_id` (опц.), `display_name` (1–128), `description` (≤2000),
`max_channels` (int≥1). **Ответ:** `AccountDetail`. **Ошибки:** `404`.

```bash
curl -sS -X PATCH "$BASE/discovery-api/parser/account-meta" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"acc1","display_name":"Основной","max_channels":400}'
```

#### PATCH /discovery-api/parser/accounts/{session_name}

Обновляет аккаунт по имени в пути. **Тело** (`AccountUpdateBody`):
`display_name` (1–128), `description` (≤2000), `max_channels` (int≥1).
**Ответ:** `AccountFullSummary`. **Ошибки:** `404`.

```bash
curl -sS -X PATCH "$BASE/discovery-api/parser/accounts/acc1" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"display_name":"Аккаунт 1","max_channels":350}'
```

#### PATCH /discovery-api/parser/accounts/{session_name}/block

Блокировка/разблокировка аккаунта администратором. **Тело** (`AccountBlockUpdate`):
`blocked` (bool, обяз.), `reason` (string ≤500, опц.). **Ответ:** `AccountFullSummary`. **Ошибки:** `404`.

```bash
curl -sS -X PATCH "$BASE/discovery-api/parser/accounts/acc1/block" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"blocked":true,"reason":"подозрение на бан"}'
```

#### DELETE /discovery-api/parser/accounts/{session_name}

Полностью удаляет аккаунт (из clump и хранилища). **Query:** `migrate` (bool, по умолч. `true`) —
переносить ли каналы на другие сессии перед удалением.
**Ответ:** `{"ok": true, "session_name": "...", "deleted": true}`. **Ошибки:** `409` (нельзя удалить).

```bash
curl -sS -X DELETE "$BASE/discovery-api/parser/accounts/acc1?migrate=true" -H "X-API-Key: $KEY"
```

#### POST /discovery-api/parser/{parser_id}/enroll-session

Регистрирует существующий `.session`-файл и добавляет его в clump.
**Тело** (`SessionBody`): `session_name` (обяз.). **Ответ:** `AccountFullSummary`.
**Ошибки:** `404` (файл сессии не найден), `409` (clump остановлен).

```bash
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/enroll-session" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"acc3"}'
```

#### POST /discovery-api/parser/{parser_id}/add-session

Добавляет сессию в clump. **Тело** (`SessionBody`): `session_name`.
**Ответ** (`SessionOpResponse`): `parser_id`, `session_name_list`, `detail`. **Ошибки:** `409` (clump остановлен).

```bash
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/add-session" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"acc4"}'
```

#### POST /discovery-api/parser/{parser_id}/remove-session

Удаляет сессию из clump. **Тело** (`SessionBody`): `session_name`.
**Ответ:** `SessionOpResponse`. **Ошибки:** `400` (нельзя удалить), `409` (clump остановлен).

```bash
curl -sS -X POST "$BASE/discovery-api/parser/PARSER_ID/remove-session" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_name":"acc4"}'
```

---

### 9.15. Парсер — задачи (actions) и PG-очередь

#### GET /discovery-api/parser/actions

Список задач (in-memory action-queue). **Query:** `status`, `parser_id`, `action_type` (все опц.),
`limit` (1–500, по умолч. 100). **Ответ** (`ActionListResponse`): `total`, `actions` (array<ActionItemResponse>).

`ActionItemResponse`: `id`, `action_type`, `parser_id`, `payload`, `status`, `progress` (object<int>),
`error`, `created_at`, `started_at`, `finished_at`.

```bash
curl -sS "$BASE/discovery-api/parser/actions?status=pending&limit=50" -H "X-API-Key: $KEY"
```

#### GET /discovery-api/parser/actions/{action_id}

Одна задача. **Ответ:** `ActionItemResponse`. **Ошибки:** `404`.

```bash
curl -sS "$BASE/discovery-api/parser/actions/ACTION_ID" -H "X-API-Key: $KEY"
```

#### GET /discovery-api/parser/queue/tasks/{task_id}

Снимок задачи PG-очереди по числовому id. **Ответ** (`TaskQueueItemResponse`):
`id` (int), `task_type_code`, `status`, `attempt_count`, `postpone_count`, `last_error`,
`last_error_code`, `payload`, `run_after`, `started_at`, `finished_at`, `last_error_at`.
**Ошибки:** `404`.

```bash
curl -sS "$BASE/discovery-api/parser/queue/tasks/12345" -H "X-API-Key: $KEY"
```

#### GET /discovery-api/parser/queue/metrics

Агрегированные метрики PG-очереди и аккаунтов (G3). Требует `USE_PG_QUEUE=true`.

**Ответ** (`MetricsResponse`):

| Поле | Тип | Описание |
|------|-----|----------|
| `queue` | object | `total`, `by_status`, `by_type`, `oldest_queued_age_seconds`, `stuck_count`, `done_last_5_min` |
| `accounts` | object | `active`, `in_cooldown`, `without_resource`, `per_op[]`, `worst_by_account[]` |
| `alerts_preview` | object | `high_postpone_count` |
| `generated_at` | string | ISO-время снимка |

**Ошибки:** `503` (PG-очередь выключена или недоступна).

```bash
curl -sS "$BASE/discovery-api/parser/queue/metrics" -H "X-API-Key: $KEY"
```

---

### 9.16. Сводная таблица всех эндпойнтов

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/health` | Живость (без ключа) |
| POST | `/discovery-api/discover` | Поиск каналов |
| POST | `/discovery-api/discover-groups` | Поиск групп |
| POST | `/discovery-api/add-channel-by-link` | Добавить канал по ссылке |
| POST | `/discovery-api/add-channel-by-link-session-file` | То же, сессия в `session_file` |
| POST | `/discovery-api/auth/qr` | Создать QR-сессию |
| GET | `/discovery-api/auth/qr/{session_id}/status` | Статус QR-сессии |
| DELETE | `/discovery-api/auth/qr/{session_id}` | Удалить QR-сессию |
| POST | `/discovery-api/bot/send-message` | Отправить сообщение ботом |
| POST | `/discovery-api/parser/start` | Запустить clump |
| POST | `/discovery-api/parser/stop/{parser_id}` | Остановить clump |
| DELETE | `/discovery-api/parser/{parser_id}` | Удалить clump |
| GET | `/discovery-api/parser/status/{parser_id}` | Статус clump |
| GET | `/discovery-api/parser/list` | Список clump |
| GET | `/discovery-api/parser/{parser_id}/channels` | Каналы clump |
| POST | `/discovery-api/parser/{parser_id}/add-channels` | Добавить каналы |
| POST | `/discovery-api/parser/{parser_id}/remove-channels` | Удалить каналы |
| GET | `/discovery-api/parser/{parser_id}/config` | Конфиг clump |
| PATCH | `/discovery-api/parser/{parser_id}/config` | Изменить конфиг clump |
| GET | `/discovery-api/parser/settings` | Глобальные настройки |
| GET | `/discovery-api/parser/accounts/all` | Все аккаунты |
| GET | `/discovery-api/parser/accounts` | Аккаунты активных clump |
| GET | `/discovery-api/parser/account-detail` | Деталь аккаунта |
| GET | `/discovery-api/parser/account-channels` | Каналы аккаунта |
| PATCH | `/discovery-api/parser/account-meta` | Метаданные аккаунта |
| PATCH | `/discovery-api/parser/accounts/{session_name}` | Обновить аккаунт |
| PATCH | `/discovery-api/parser/accounts/{session_name}/block` | Блокировка аккаунта |
| DELETE | `/discovery-api/parser/accounts/{session_name}` | Удалить аккаунт |
| POST | `/discovery-api/parser/{parser_id}/enroll-session` | Зачислить сессию |
| POST | `/discovery-api/parser/{parser_id}/add-session` | Добавить сессию |
| POST | `/discovery-api/parser/{parser_id}/remove-session` | Удалить сессию |
| GET | `/discovery-api/parser/actions` | Список задач |
| GET | `/discovery-api/parser/actions/{action_id}` | Задача по id |
| GET | `/discovery-api/parser/queue/tasks/{task_id}` | Задача PG-очереди |
| GET | `/discovery-api/parser/queue/metrics` | Метрики очереди (G3) |

---

## 10. Зафиксированные решения (бывшие «открытые»)

Решения ниже обязательны для реализации. Источник порогов алертов — `app_balance/queue/monitoring/config.py` (env с дефолтами).

### 10.1. RPH UI → backend per-op

| Решение | Детали |
|---------|--------|
| **Модель UI** | Одно поле «RPH (задач/час)» на строку `task_types.code` |
| **Маппинг на backend** | При сохранении PATCH `task-types/{code}` backend обновляет `rph_limit` **primary op** — op с максимальным `units_per_execution` в `task_type_ops` для роли `primary` (или `target` для dual-account типов, если primary нет) |
| **Не в UI** | Прямое редактирование `resource_op_types` скрыто; read-only детализация per-op — в expand строки аккаунта (§5.3) |
| **До TBI API** | Вкладка RPH **read-only**: counts из `queue.by_type`, usage из `accounts.per_op[]`; формы редактирования disabled с подсказкой «ожидается API §9.4» |

### 10.2. Права доступа

| Решение | Детали |
|---------|--------|
| **MVP** | Достаточно существующего `requireAdminAuth` платформы (любой авторизованный admin) |
| **Отдельная роль** | Не вводить `balancer_operator` в MVP |
| **Impersonation** | Доступ разрешён, если impersonation даёт admin-сессию (те же правила, что у остальной админки) |

### 10.3. История метрик (sparkline)

| Решение | Детали |
|---------|--------|
| **MVP** | **Out of scope** — только текущий снимок + `generated_at` |
| **Опционально в сессии** | In-memory ring buffer последних 120 снимков (30 с × 1 ч) **без persist** — только если успеем; не блокирует приёмку |
| **Алерт «рост очереди»** | В MVP **не считаем на клиенте** (нужна история 900 с). Показываем только `alerts_preview.high_postpone_count` + правила из §10.4 по текущему снимку |

### 10.4. Действия над задачами (MVP)

| Решение | Детали |
|---------|--------|
| **MVP** | **Только просмотр** (modal детали). Retry/cancel/requeue — **Phase 2**, после API `POST /queue/tasks/{id}/retry` (TBI) |
| **Кто может (Phase 2)** | Admin с `requireAdminAuth` |

### 10.5. Источник списка задач (MVP)

| Решение | Детали |
|---------|--------|
| **Phase 1 (до TBI)** | Таблица «Последние задачи» = **`GET /parser/actions?limit=100`** (in-memory bulk). Для PG-задач add/remove — polling **`GET /queue/tasks/{id}`** по `task_ids[]` из async-ответов |
| **Phase 2** | Переключить таблицу на **`GET /parser/queue/tasks`** (контракт §9.4.1) |
| **NF-1** | Пагинация client-side; не грузить 10k — limit 100–500, фильтры client-side на загруженном наборе |

### 10.6. MVP vs Phase 2 (приоритет сдачи)

| Phase | Содержание | Критерии §8 |
|-------|------------|-------------|
| **Phase 1** | Очередь KPI + alerts + actions table + task detail modal; Аккаунты list/expand/QR/block; RPH read-only + clump config read-only | №1–3, 5–7 (RPH edit — исключение) |
| **Phase 2** | PATCH task-types, GET queue/tasks list, retry/cancel, sparkline (опц.) | №4 полностью |

### 10.7. Scope clump в UI

| Функция | MVP |
|---------|-----|
| `GET /parser/list`, `/status/{id}` | **Да** — контекст, фильтры |
| `POST /parser/start`, `/stop`, add/remove channels | **Нет** — ops через n8n/API; в UI только мониторинг |
| Enroll после QR | **Да** |
| Редактирование `PATCH …/config` | **Да**, ограниченно: `add_channels_per_hour`, `max_channels_per_session` (§6.6) |

### 10.8. Scope вкладки «Аккаунты»

| Функция | MVP |
|---------|-----|
| Block/unblock | **Да** + confirm + поле reason |
| DELETE account | **Да** + confirm «Удалить аккаунт {name}? Каналы будут перенесены (migrate=true)» |
| PATCH meta (`display_name`, `max_channels`) | **Да** — inline или modal |
| Показ `session_string` после QR | **Нет** — только `session_file`, `phone`, `user_name` |

---

## 11. Контракты данных (дополнения к §9.5)

### 11.1. Канонический enum `task_status`

Из PostgreSQL (`DB/BD_schema.sql`):

`queued`, `scheduled`, `in_progress`, `retry`, `done`, `failed`, `cancelled`, `stuck`

**Активные** (учитываются в `queue.total`): все кроме terminal `done`, `failed`, `cancelled`.

### 11.2. Структура `queue.by_type`

Вложенный объект: **каждый тип → все статусы с count > 0**.

```json
{
  "parser_add_channel": {"queued": 10, "retry": 2},
  "move_channel": {"in_progress": 1, "scheduled": 5}
}
```

Источник: VIEW `v_queue_size_by_type` (`task_type_code`, `status`, `tasks_count`).

### 11.3. Блок «Проблемы» — правила и пороги

**Из API сейчас:** только `alerts_preview.high_postpone_count` (число задач в VIEW `v_high_postpone_tasks`).

**Дополнительно считаем на клиенте** из одного снимка `queue/metrics` (константы = дефолты backend):

| Код алерта | Условие | Severity | Текст UI (пример) |
|------------|---------|----------|-------------------|
| `high_postpone` | `alerts_preview.high_postpone_count > 0` | warning | «{n} задач с частыми отложениями (≥10)» |
| `oldest_queue_stale` | `queue.oldest_queued_age_seconds > 3600` | warning | «Старейшая задача ждёт {age} (порог 1 ч)» |
| `no_active_accounts` | `accounts.active === 0` | error | «Нет активных аккаунтов» |
| `stuck_no_progress` | `queue.stuck_count > 0 && queue.done_last_5_min === 0` | error | «Зависшие задачи, нет завершений за 5 мин» |
| `queue_no_progress` | `queue.total > 0 && queue.done_last_5_min === 0` | error | «Очередь не двигается» |
| `accounts_without_resource` | `accounts.without_resource === accounts.active && accounts.active > 0` | warning | «У всех активных аккаунтов исчерпан ресурс» |

**Не в MVP на клиенте:** `queue_growth` (нужна история 900 с), `task_type_error_spike`, `account_error_spike` (нужны VIEW error_rate — **TBI** поле в metrics или отдельный эндпойнт).

Порог **high_postpone:** `postpone_count >= 10` (`ALERT_HIGH_POSTPONE_MIN`, default 10).

### 11.4. Расширенная схема задачи (TBI `GET /queue/tasks`)

Phase 2 list/detail должен возвращать (дополнение к §9.5.3):

| Поле | Тип | Описание |
|------|-----|----------|
| `priority` | int | Из `task_queue.priority` |
| `account_id` | int\|null | Назначенный аккаунт |
| `session_name` | string\|null | JOIN accounts |
| `created_at` | string (ISO) | |
| `task_type_name` | string | Из `task_types.name` |
| `payload.action_id` | string\|null | Связь с in-memory action |

**Query list (TBI):** `status`, `task_type_code`, `account_id`, `has_error` (bool), `min_postpone`, `limit` (1–500), `offset`, `sort` (`created_at_desc` default).

### 11.5. Связь `action_id` ↔ PG `task_id`

При async add/remove channels:

- HTTP-ответ: `action_id` (hex) + `task_ids[]` (int)
- В PG `task_queue.payload` сохраняется `"action_id": "<hex>"` (см. `discovery_api/queue/producer.py`)
- UI: bulk action → строка actions; клик → modal со списком linked `task_ids` + polling каждого

### 11.6. История попыток `task_attempts` (TBI)

```
GET /discovery-api/parser/queue/tasks/{task_id}/attempts?limit=50
```

| Поле | Тип |
|------|-----|
| `id` | int |
| `attempt_number` | int |
| `status` | `running` \| `success` \| `error` \| `timeout` |
| `error_code` | string\|null |
| `error_message` | string\|null |
| `started_at`, `finished_at` | ISO |

MVP: в modal задачи показывать только `attempt_count`, `postpone_count`, `last_error` — без таблицы attempts.

### 11.7. Usage RPH «по типу задачи» (§6.3 read-only)

| Метрика | Источник |
|---------|----------|
| Задач в очереди по типу | `queue.by_type[code]` — сумма статусов |
| Фактический RPH usage | **Только per-op** (`accounts.per_op[]`), не агрегируется по task_type в API |
| UI | Колонка «в очереди» из `by_type`; tooltip **«Telethon ops»** — статичный маппинг **§11.8** (не TBI) |

### 11.8. Маппинг `task_types.code` → Telethon ops (tooltip RPH)

Статичная таблица в коде UI (`src/config/balancer-task-type-ops.ts`), источник истины — seed `DB/A9_seed.sql`. Tooltip на строке RPH: «Ops, затронутые типом задачи (primary/target)».

| `task_type_code` | Роль | `op_code` | units/выполнение |
|------------------|------|-----------|------------------|
| `parser_add_channel` | primary | `get_entity` | 2 |
| `parser_add_channel` | primary | `channels.JoinChannel` | 2 |
| `parser_add_channel` | primary | `channels.GetFullChannel` | 1 |
| `parser_add_channel` | primary | `channels.GetParticipant` | 1 |
| `parser_remove_channel` | primary | `get_entity` | 2 |
| `parser_remove_channel` | primary | `channels.GetFullChannel` | 1 |
| `parser_remove_channel` | primary | `channels.LeaveChannel` | 2 |
| `move_channel` | source | `channels.GetParticipant` | 1 |
| `move_channel` | target | `get_entity` | 2 |
| `move_channel` | target | `channels.JoinChannel` | 2 |
| `move_channel` | target | `channels.GetFullChannel` | 1 |
| `move_channel` | target | `channels.GetParticipant` | 1 |
| `collect_extra_data` | primary | `get_entity` | 2 |
| `collect_extra_data` | primary | `channels.JoinChannel` | 2 |
| `collect_extra_data` | primary | `channels.GetFullChannel` | 1 |
| `collect_extra_data` | primary | `iter_messages` | 1 |
| `collect_extra_data` | primary | `channels.GetParticipants` | 1 |
| `collect_extra_data` | primary | `channels.LeaveChannel` | 2 |
| `update_channel` | primary | *(как `collect_extra_data`)* | *(те же)* |
| `update_channel` | primary | `channels.GetParticipants` | 1 |

**Primary op для PATCH RPH** (§10.1): op с max `units_per_execution` среди `primary`; для `move_channel` — среди `target`.

**Не показывать в tooltip:** `add_channels` / `remove_channels` как action_type — это bulk actions, не `task_types`.

### 11.9. G6★ «RPH автоматически снижен» (Phase 2)

Backend: таблица `resource_op_rph_adjustments`. TBI в `GET task-types/{code}`:

```json
{
  "code": "parser_add_channel",
  "rph_limit_effective": 18,
  "rph_limit_default": 27,
  "rph_auto_reduced": true,
  "rph_reduced_at": "2026-06-25T10:00:00Z"
}
```

Сброс: `PATCH …/task-types/{code}` body `{ "reset_rph_to_default": true }`.

MVP: блок скрыт.

### 11.10. Пример `GET /parser/settings` (фрагмент)

```json
{
  "settings": {
    "max_channels_per_session": 500,
    "add_channels_per_hour": 0,
    "rebalance_enabled": false,
    "rebalance_idle_start_hour": 2,
    "rebalance_idle_end_hour": 6
  },
  "descriptions": {
    "max_channels_per_session": "Лимит каналов на один аккаунт (сессию)",
    "add_channels_per_hour": "Лимит успешных добавлений каналов на аккаунт в час (0 = без лимита)"
  }
}
```

На вкладке RPH: секция «Глобальные дефолты» — read-only таблица key / value / description.

### 11.11. `ClumpConfigResponse.config` — поля для UI

| Поле | Вкладка | MVP edit |
|------|---------|----------|
| `add_channels_per_hour` | RPH | PATCH |
| `max_channels_per_session` | RPH | PATCH |
| `rebalance_*` | RPH | read-only (PG-балансер отключает idle-rebalance) |
| `auto_migrate`, reconnect-* | — | read-only |

Выбор clump: dropdown `parser_id` из `GET /parser/list`; если пусто — empty state «Нет активных clump».

### 11.12. Поле `channels` в metrics (backend уже отдаёт)

Backend `MetricsSnapshot.to_response_dict()` включает `channels` (G7), но Pydantic `MetricsResponse` API может отрезать. **TBI:** добавить в OpenAPI. UI (Phase 2): KPI «Загрузка каналов fleet» — `channels.usage_percent`.

### 11.13. Контракт overlay PG cooldown (входящие данные)

Канон для фронтенда — **§5.3.1**. Backend-справка: [`account-cooldown-overlay-api.md`](account-cooldown-overlay-api.md).

**Zod-скетч** (`src/schemas/balancer/account-queue-overlay.ts`):

```typescript
const queueStatusSchema = z.enum([
  'active', 'cooldown', 'disabled', 'banned', 'error',
]).nullable();

export const accountQueueOverlaySchema = z.object({
  queue_status: queueStatusSchema,
  cooldown_until: z.string().datetime().nullable(),
  cooldown_remaining_seconds: z.number().int().nonnegative().nullable(),
  available_at: z.string().datetime().nullable(),
  available_in_seconds: z.number().int().nonnegative().nullable(),
  flood_until: z.number().nullable(),
  current_task_id: z.number().int().positive().nullable(),
  last_error: z.string().nullable(),
  last_error_at: z.string().datetime().nullable(),
  is_enabled: z.boolean().nullable(),
});

export const accountAllListResponseSchema = z.object({
  total: z.number().int(),
  generated_at: z.string().datetime(),
  accounts: z.array(accountFullSummarySchema), // extends overlay
});
```

**Хелперы UI:**

| Функция | Вход | Выход |
|---------|------|-------|
| `isAccountInCooldown(row)` | overlay | `available_in_seconds > 0` или `queue_status === 'cooldown'` |
| `formatAvailableAt(row, now)` | `available_at`, `available_in_seconds` | «через 4 мин 30 с» / «—» |
| `getDispatchBadge(row)` | `queue_status`, `is_enabled`, `admin_blocked` | variant + label (§12.8) |

**Expand row аккаунта:** блок «Cooldown / FloodWait» — три строки: `queue_status`, `cooldown_until` (PG), `flood_until` (runtime unix → локальное время). Если оба `null` — «Доступен для dispatch».

**Согласованность KPI:** при refresh вкладки «Аккаунты» запрашивать **параллельно** `/queue/metrics` и `/accounts/all`; в footer показывать `generated_at` из **обоих** ответов (metrics + accounts), если различаются — warning «снимки разного времени».

---

## 12. UX/UI — спецификация

### 12.1. Навигация и роуты (Next.js App Router)

| Элемент | Значение |
|---------|----------|
| Sidebar | Отдельный пункт **«Балансировщик»**, иконка `Scale` (lucide), после основных разделов админки |
| Базовый route | `/admin/balancer` |
| Подвкладки | **Tabs внутри страницы** (не отдельные routes): `?tab=queue` \| `accounts` \| `rph`, default `queue` |
| Deep link задачи | `/admin/balancer?tab=queue&taskId=12345` — открывает modal |

### 12.2. QR-flow

| Параметр | Значение |
|----------|----------|
| `qr_url` | Строка `tg://login?token=...` — **генерировать QR на клиенте** (lib `qrcode` или `react-qr-code`), не `<img src=qr_url>` |
| Размер QR | 256×256 px, error correction M |
| Polling | каждые **3 с**, timeout **120 с** |
| Хранение | `session_id` только в React state / URL searchParams; **не localStorage** |
| Статусы UI | см. §5.4; `expired` → кнопка «Создать новый QR» |
| Mobile | Modal на весь экран |

### 12.3. Автообновление

| Параметр | Значение |
|----------|----------|
| Интервал | **30 с** на всех подвкладках |
| Пауза | `document.visibilityState === 'hidden'` → stop timer |
| Ручное | Кнопка «Обновить» сбрасывает timer |
| Индикатор | «Обновлено: {generated_at локальное время}» — для вкладки **Аккаунты** брать `generated_at` из `/accounts/all`; для **Очередь** — из `/queue/metrics` |

### 12.4. Деградация при ошибках API

| Сценарий | Поведение |
|----------|-----------|
| `queue/metrics` → 503 | Banner: «PG-очередь недоступна»; вкладка Очередь — empty; Аккаунты/RPH работают если свои API OK |
| `accounts/all` fail, metrics OK | Таблица аккаунтов — error state; KPI очереди OK |
| Partial | Блочные `Alert` + retry button per block |

### 12.5. Таблицы и детали

| Элемент | Решение |
|---------|---------|
| Задачи | Modal (shadcn `Sheet`/`Dialog`), не отдельная page |
| Payload preview | JSON pretty-print; для `parser_add_channel` highlight `channel_ref`, `parser_id` |
| Аккаунты | Expand row: per-op badges + блок cooldown (§11.13) + `account-channels` lazy load |
| Cooldown countdown | Колонка «Освободится»: `useInterval(1s)` декремент от `available_in_seconds`; при 0 — «сейчас»; resync при refresh 30 с |
| Dispatch vs runtime | Два бейджа в строке: dispatch (`queue_status`) и runtime (`status`); не сливать в один |
| Per-op collapsed | Badge «{exhausted}/{total} ops исчерпано» из `worst_by_account` |
| Фильтры задач | Client-side на загруженных 100 строк (MVP) |
| Charts | **Recharts** (стек проекта): одна **bar chart** `by_status`; pie optional |

### 12.6. Empty states (русский)

| Состояние | Текст | CTA |
|------------|-------|-----|
| Нет clump | «Нет активных clump. Запустите парсер через API или n8n.» | ссылка на docs (внутренняя якорь §9.11) |
| Нет аккаунтов | «Telegram-аккаунты не найдены» | «Добавить через QR» |
| Очередь пуста | «Очередь пуста» | — |
| PG off | «PG-очередь отключена на сервере балансировщика (USE_PG_QUEUE=false)» | — |

### 12.7. Confirm dialogs

| Действие | Confirm |
|----------|---------|
| Block account | Dialog + обязательное поле «Причина» (≤500) |
| Unblock | Без confirm |
| Delete account | «Удалить аккаунт {session_name}? Каналы будут перенесены на другие сессии.» |
| DELETE QR session | Без confirm (отмена QR) |

### 12.8. Локализация статусов и ошибок

**Статусы задач:**

| code | UI label |
|------|----------|
| `queued` | В очереди |
| `scheduled` | Запланирована |
| `in_progress` | Выполняется |
| `retry` | Повтор |
| `done` | Завершена |
| `failed` | Ошибка |
| `cancelled` | Отменена |
| `stuck` | Зависла |

**`last_error_code`:** в колонке — **русское описание** (маппинг из §9.5.4); raw код — в tooltip. Функция `getTaskErrorLabel(code)`.

**Статусы dispatch (`queue_status`, PG):**

| code | UI label | Badge variant |
|------|----------|---------------|
| `active` | Активен | default / green |
| `cooldown` | Cooldown | warning |
| `disabled` | Выключен | secondary |
| `banned` | Забанен | destructive |
| `error` | Ошибка | destructive |

**Статусы runtime (`status`, clump):**

| code | UI label |
|------|----------|
| `healthy` | В норме |
| `flood_wait` | FloodWait |
| `offline` | Оффлайн |
| `starting` | Запуск |

**Ошибки аккаунта (`last_error` в overlay):** `flood_wait` → «FloodWait Telegram»; `join_pending` → «Ожидание join»; иначе raw в tooltip.

---

## 13. Интеграция в lidogen_site

### 13.1. Переменные окружения (server-only)

| Variable | Назначение | Пример prod |
|----------|------------|-------------|
| `DISCOVERY_API_BASE_URL` | Base URL | `https://lidogen-balancer-tg-prod.web.oboyma.ai` |
| `DISCOVERY_API_KEY` | `X-API-Key` | secret в Vercel / GitHub Actions |
| `DISCOVERY_API_TIMEOUT_MS` | Timeout fetch | `10000` |

**Staging:** отдельный инстанс не выделен; dev/staging UI использует **mock BFF** или prod read-only с ограниченным key (решение команды infra). Локально: `DISCOVERY_API_BASE_URL=http://127.0.0.1:8100` при SSH-туннеле.

### 13.2. BFF `/api/admin/balancer/*` — полная таблица роутов

Базовый префикс Next.js: **`/api/admin/balancer`**. Все routes — **server-only**, `requireAdminAuth()`, заголовок `X-API-Key` на upstream из env.

| BFF method | BFF path | Upstream | Query / body | Вкладка UI |
|------------|----------|----------|--------------|------------|
| GET | `/health` | `GET /health` | — | общее |
| GET | `/metrics` | `GET /discovery-api/parser/queue/metrics` | — | Очередь, RPH |
| GET | `/actions` | `GET /discovery-api/parser/actions` | `status`, `parser_id`, `action_type`, `limit` | Очередь |
| GET | `/actions/[actionId]` | `GET /discovery-api/parser/actions/{action_id}` | — | Очередь modal |
| GET | `/tasks/[taskId]` | `GET /discovery-api/parser/queue/tasks/{task_id}` | — | Очередь modal |
| GET | `/parsers` | `GET /discovery-api/parser/list` | — | Очередь, Аккаунты, RPH |
| GET | `/parsers/[parserId]` | `GET /discovery-api/parser/status/{parser_id}` | — | контекст |
| GET | `/parsers/[parserId]/channels` | `GET /discovery-api/parser/{parser_id}/channels` | — | опц. |
| GET | `/parsers/[parserId]/config` | `GET /discovery-api/parser/{parser_id}/config` | — | RPH |
| PATCH | `/parsers/[parserId]/config` | `PATCH /discovery-api/parser/{parser_id}/config` | JSON body | RPH |
| GET | `/settings` | `GET /discovery-api/parser/settings` | — | RPH |
| GET | `/accounts` | `GET /discovery-api/parser/accounts/all` | — | Аккаунты |
| GET | `/accounts/[session]/detail` | `GET /discovery-api/parser/account-detail` | `session_name`, `parser_id?` | Аккаунты expand |
| GET | `/accounts/[session]/channels` | `GET /discovery-api/parser/account-channels` | `session_name`, `parser_id?` | Аккаунты expand |
| PATCH | `/accounts/[session]` | `PATCH /discovery-api/parser/accounts/{session_name}` | `display_name`, `description`, `max_channels` | Аккаунты |
| PATCH | `/accounts/[session]/block` | `PATCH /discovery-api/parser/accounts/{session_name}/block` | `blocked`, `reason?` | Аккаунты |
| DELETE | `/accounts/[session]` | `DELETE /discovery-api/parser/accounts/{session_name}` | `migrate=true` (default) | Аккаунты |
| POST | `/parsers/[parserId]/enroll-session` | `POST /discovery-api/parser/{parser_id}/enroll-session` | `{ session_name }` | Аккаунты |
| POST | `/parsers/[parserId]/sessions` | `POST /discovery-api/parser/{parser_id}/add-session` | `{ session_name }` | Аккаунты |
| POST | `/parsers/[parserId]/sessions/remove` | `POST /discovery-api/parser/{parser_id}/remove-session` | `{ session_name }` | Аккаунты |
| POST | `/auth/qr` | `POST /discovery-api/auth/qr` | `{ session_name? }` | Аккаунты QR |
| GET | `/auth/qr/[sessionId]/status` | `GET /discovery-api/auth/qr/{session_id}/status` | — | QR poll |
| DELETE | `/auth/qr/[sessionId]` | `DELETE /discovery-api/auth/qr/{session_id}` | — | QR cancel |

**Не проксировать в MVP UI** (нет кнопок): `POST /parser/start`, `stop`, `add-channels`, `remove-channels`, `/discover`, `/bot/*`.

**Phase 2 BFF (TBI):**

| BFF | Upstream |
|-----|----------|
| GET `/queue/tasks` | `GET /discovery-api/parser/queue/tasks` |
| GET `/queue/task-types` | `GET …/queue/task-types` |
| PATCH `/queue/task-types/[code]` | `PATCH …/queue/task-types/{code}` |
| GET `/tasks/[taskId]/attempts` | `GET …/queue/tasks/{id}/attempts` |

**Auth:** каждый handler вызывает `requireAdminAuth()` до proxy.

**Кэш:** `Cache-Control: no-store` для metrics/actions.

**Retry BFF:** 1 retry на 502/503 с backoff 500 ms.

**QR rate limit:** не чаще 1 create / 5 s на user session (debounce на BFF).

### 13.3. TypeScript и Zod

| Решение | Детали |
|---------|--------|
| Расположение | `src/schemas/balancer/` — Zod-схемы `MetricsResponse`, `AccountFullSummary`, `AccountQueueOverlay`, `TaskQueueItem`, … |
| Overlay | `accountQueueOverlaySchema` — §11.13; `AccountFullSummary` = runtime fields + overlay |
| Синхронизация | Ручная по §9.5 + §9.5.8 + §11.13; CI script `pnpm run sync:balancer-openapi` (optional, Phase 2) |
| Формы RPH | Zod: `rph > 0`, `priority` int −1000…1000, `min_available_resource_percent` 0–100, `target_queue_size` ≥ 0 |
| `session_name` | `z.string().regex(/^[A-Za-z0-9_-]{1,64}$/, 'Имя сессии: латиница, цифры, _ и -, до 64 символов')` |

### 13.4. Тестирование

| Тип | Подход |
|-----|--------|
| Unit | Zod + `getTaskErrorLabel` + alert rules (§11.3) |
| Route handlers | Mock `fetch` discovery-api |
| E2E | KPI загрузка с mock BFF; **QR-flow без E2E** (manual QA checklist) |
| §8.7 prod parity | Manual QA checklist + optional integration test с `DISCOVERY_API_KEY` в CI secret |
| Mock layer | По правилам проекта: **`src/services/balancer/`** + env `USE_BALANCER_MOCK=true` для UI dev без prod |

### 13.5. Audit платформы

Block/RPH changes: log в существующий admin audit log платформы (если есть hook) — **желательно Phase 2**. Backend audit RPH — таблица PG, API TBI.

---

## 14. Спецификация TBI-эндпойнтов (§9.4, детализация)

### 14.1. `GET /discovery-api/parser/queue/tasks`

Query: `status`, `task_type_code`, `account_id`, `has_error`, `min_postpone`, `limit`, `offset`, `sort`.

Response:

```json
{
  "total": 42,
  "items": [{ "...": "TaskQueueItemExtended §11.4" }]
}
```

### 14.2. `GET /discovery-api/parser/queue/task-types`

Response: array всех полей §6.3 + `name`, `description`, `rph_limit_effective`, `rph_limit_default`, `primary_op_code`.

### 14.3. `PATCH /discovery-api/parser/queue/task-types/{code}`

Body: любое подмножество editable полей §6.3 + `reset_rph_to_default: bool`.

### 14.4. `GET /discovery-api/parser/queue/tasks/{id}/attempts`

См. §11.6.

### 14.5. Реализация `task-types` API

Спецификация и статус реализации: [`task-types-api-tz.md`](task-types-api-tz.md).

---

*Конец документа.*
