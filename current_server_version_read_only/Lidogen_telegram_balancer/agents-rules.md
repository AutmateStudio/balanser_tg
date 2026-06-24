# agents-rules.md — правила для ИИ-агентов (Lidogen Telegram Balancer)

> Документ для Cursor, Claude Code и других агентов, работающих **в этом репозитории**.
> Общие правила монорепозитория (ветки, деплой, язык) — в `../AGENTS.md`, если применимо.
> Здесь — **структура на dev-сервере**, **обязательные правила тестирования** и команды запуска.

---

## Язык

Диалог, комментарии к задачам и отчёты агента — **на русском**. Имена в коде — латиница.

---

## Правило С — структура репозитория на dev-сервере

### Канонический путь

Dev-сервер приложения: **`vps-101`**, пользователь **`ubuntu`**.

```text
/home/ubuntu/Lidogen_telegram_balancer/     # корень репозитория (PG Queue Balancer)
/home/ubuntu/Lidogen_telegram_balancer/standalone_discovery/   # discovery + Telethon API
```

Все команды ниже для Linux предполагают этот путь, если не указано иное.

### Структура корня (`~/Lidogen_telegram_balancer`)

Зафиксировано по `ls` на `ubuntu@vps-101` (2026-06-19):

```text
~/Lidogen_telegram_balancer/
├── DB/                      # SQL-схема, seed, миграции очереди
├── Dockerfile
├── Makefile
├── README.md
├── agents-rules.md          # этот файл — правила для агентов
├── app_balance/             # PG Queue Balancer: queue/, queue_worker.py
├── balancer/
├── docker-compose.yml       # postgres local, migrate, test, queue-worker
├── docs(plan)/              # планы спринтов, ТЗ, доступ к БД
├── pytest.ini
├── requirements.txt
├── requirements-dev.txt     # pytest, pytest-asyncio
├── scripts/                 # migrate_queue.sh, run_all_tests.sh, sync_accounts_to_pg.py, e2e_d12/
├── standalone_discovery/    # см. подраздел ниже
└── tests/                   # unit + integration тесты очереди (PG)
```

Файлы конфигурации (не в `ls`, но обязательны на dev): `.env` (из `.env.example`), опционально `.env.example`.

### Структура `standalone_discovery/`

```text
~/Lidogen_telegram_balancer/standalone_discovery/
├── discovery_api/           # FastAPI, SessionClump, action_queue
├── tests/                   # тесты discovery (отдельный pytest suite)
├── docs/
├── n8n/
├── Dockerfile
├── docker-compose.yml       # compose discovery (отдельный от корневого)
├── requirements.txt
├── README.md
├── USAGE.md
├── check_health_urls.py
├── test_endpoints.py
├── Client1.session          # Telethon-сессии (локальные артеfacts)
├── TEST_MEGAGROUP.session
├── Telegram-Parser-Gleb-2.conf
└── vpn_telegram_gleb.conf
```

> **Не коммитить в git:** `*.session`, локальные `.conf` с секретами, `__pycache__/`.

### Обязательное обновление структуры

Если в задаче **добавлен, удалён или перенесён** файл или каталог (в корне или в `standalone_discovery/`), агент **в той же задаче** обновляет раздел **«Структура корня»** и/или **«Структура standalone_discovery»** в этом файле.

- новый модуль → добавить строку в дерево;
- удалённый каталог → убрать из дерева;
- перенос → отразить новый путь;
- в отчёте о задаче одной строкой: «обновлён agents-rules.md — структура».

Не откладывать на «потом»: устаревшая структура ломает команды агентов на `vps-101`.

---

## Правило SF — доставка на vps-101 через SFTP (не git)

На dev-сервер код попадает **по SFTP с Windows**, а не через `git pull`. Агент и разработчик учитывают это в каждой задаче.

### Куда заливать

| Локально (Windows) | На сервере |
|--------------------|------------|
| `Lidogen_telegram_balancer\` | `/home/ubuntu/Lidogen_telegram_balancer/` |

Режим SFTP-клиента: **синхронизировать изменённые файлы**, сохраняя структуру каталогов. Корень на сервере — `~/Lidogen_telegram_balancer`, не вложенный `Lidogen_telegram_balancer/Lidogen_telegram_balancer/`.

### Что не перезаписывать на сервере

- **`.env`** — только на сервере, с реальными паролями; не заливать с локальной машины поверх.
- **`standalone_discovery/*.session`**, локальные **`*.conf`** с секретами — артеfacts сервера.
- **`__pycache__/`**, **`.pytest_cache/`** — не нужны.

### После каждой заливки по SFTP — чеклист на vps-101

```bash
cd ~/Lidogen_telegram_balancer

# 1. Shell-скрипты: убрать CRLF (Windows → Linux)
sed -i 's/\r$//' scripts/*.sh

# 2. Если менялись Dockerfile, requirements*, docker-compose.yml — пересборка образа
docker compose build --no-cache test

# 3. Если менялись только .py / tests — достаточно build без --no-cache
# docker compose build test

# 4. Полный прогон тестов
docker compose run --rm test
```

Если менялся только **`standalone_discovery/requirements.txt`** (новый pip-пакет) — **обязательно** `docker compose build --no-cache test`, иначе старый слой pip останется без новой зависимости.

### Что агент указывает в отчёте задачи

Список **файлов/каталогов для SFTP** (явные пути), например:

```text
Залить на vps-101:
  app_balance/queue/task_queue.py
  tests/test_task_queue_postpone.py
  standalone_discovery/requirements.txt
  docker-compose.yml
  Dockerfile
После заливки: sed -i 's/\r$//' scripts/*.sh && docker compose build --no-cache test && docker compose run --rm test
```

Не писать «сделайте git pull» — на vps-101 используется SFTP.

### Типовые наборы файлов

| Задача | Залить | На сервере |
|--------|--------|------------|
| B5 postpone | `app_balance/`, `tests/test_task_queue_*`, `.env.example` | `build test` → `run --rm test` |
| Docker / все тесты | `Dockerfile`, `docker-compose.yml`, `requirements*.txt`, `standalone_discovery/requirements.txt`, `.gitattributes`, `scripts/run_all_tests.sh` | `build --no-cache test` |
| Только discovery | `standalone_discovery/` (без `.session`) | `build test` если менялся `requirements.txt` |
| SQL / миграции | `DB/`, `scripts/migrate_queue.sh` | `sed` на `.sh`, затем `docker compose run --rm migrate` |

---

## Правило D — Docker Compose и полный pytest

### Что запускает Docker

Сервисы **`test`** и **`test-local`** в [`docker-compose.yml`](docker-compose.yml) вызывают **pytest напрямую** (exec-форма, без bash-скрипта):

```text
pytest tests/ standalone_discovery/tests/ -v --tb=short
```

На хосте тот же набор: `bash scripts/run_all_tests.sh`.

Ожидаемый объём: **~276 тестов** (91 очередь + ~185 discovery). Образ [`Dockerfile`](Dockerfile) ставит зависимости из `requirements-dev.txt` **и** `standalone_discovery/requirements.txt`.

| Сервис | Когда | БД |
|--------|-------|-----|
| `test` | dev/staging через Tailscale | `QUEUE_DATABASE_URL` из `.env` → `vps-100` |
| `test-local` | profile `local`, без Tailscale | postgres в compose |

### Обязательное обновление Docker при изменении тестов

Если в задаче затронуты тесты, зависимости или layout — агент **в той же задаче** проверяет и при необходимости обновляет:

| Изменение | Что обновить |
|-----------|--------------|
| Новый каталог с тестами (третий suite, перенос `tests/`) | [`scripts/run_all_tests.sh`](scripts/run_all_tests.sh) — список путей pytest |
| Новый pip-пакет для тестов discovery | [`standalone_discovery/requirements.txt`](standalone_discovery/requirements.txt) + пересборка образа (`httpx2` для `TestClient`) |
| Новый pip-пакет для тестов очереди | [`requirements-dev.txt`](requirements-dev.txt) или [`requirements.txt`](requirements.txt) |
| Новые env-переменные для pytest | `environment:` сервисов `test` / `test-local` в [`docker-compose.yml`](docker-compose.yml) |
| Отдельный сценарий (только integration, smoke) | новый сервис в compose **или** аргументы в `run_all_tests.sh` — не ломать дефолт «все тесты» |

**Правило по умолчанию:** сервисы `test` / `test-local` всегда гоняют **полный** набор (`tests/` + `standalone_discovery/tests/`). Точечный pytest — только вручную, не в compose.

После правок Docker-файлов агент пересобирает образ и прогоняет:

```bash
cd ~/Lidogen_telegram_balancer
docker compose build
docker compose run --rm test
# или локально: docker compose --profile local run --rm test-local
```

В отчёте: «обновлён docker-compose / run_all_tests.sh / Dockerfile» — если менялось.

### CRLF в `*.sh` (ошибка `set: pipefail: invalid option name`)

Если на Linux при `docker compose run --rm test` или migrate видно:

```text
scripts/….sh: line N: set: pipefail: invalid option name
```

Причина: файл сохранён с окончаниями строк **Windows (CRLF)**. Bash читает `pipefail\r` вместо `pipefail`.

**Профилактика:** [`.gitattributes`](.gitattributes) — `*.sh text eol=lf`.

**На сервере после SFTP с Windows** (см. правило SF):

```bash
cd ~/Lidogen_telegram_balancer
sed -i 's/\r$//' scripts/*.sh
```

Сервисы `test` / `test-local` вызывают **pytest напрямую** (exec-форма в compose), без bash-скрипта — CRLF в `run_all_tests.sh` на Docker не влияет. Скрипт нужен для запуска на хосте (`bash scripts/run_all_tests.sh`).

---

## Правило Т — тесты обязательны и всегда полный прогон

### Когда писать тесты

| Тип изменения | Unit-тесты | Integration-тесты |
|---------------|------------|-------------------|
| Логика репозитория, SQL, dispatch, воркер | **Обязательны** | **Обязательны** (живая PG) |
| Новый публичный метод / переход статуса в очереди | **Обязательны** | **Обязательны** |
| Конфиг из env (`WorkerConfig`, feature flags) | **Обязательны** | По необходимости |
| Только документация / комментарии | Не требуются | Не требуются |

**Unit-тесты** — без PostgreSQL, с mock/fake (`tests/test_*_unit.py`, `@pytest.mark` без `integration`).

**Integration-тесты** — против живой БД, маркер `@pytest.mark.integration`, фикстура `pg_pool`, декоратор `@requires_pg` из [`tests/conftest.py`](tests/conftest.py). Требуют `QUEUE_DATABASE_URL`.

Порядок по задаче (как в плане спринта): **сначала тест-кейсы / критерии приёмки → код → полный прогон**.

### Запрещено объявлять задачу готовой, если

- добавлена логика без unit-тестов там, где они применимы;
- затронуты `app_balance/queue/*`, `queue_worker`, миграции или SQL — но integration-тесты не прогнаны на dev-БД (или явно не указано, почему пропущены);
- прогнаны **только** точечные файлы, а полный suite не проверен.

### Всегда полный прогон

При любой работе с тестами агент **обязан** запускать **полный** набор:

```text
tests/                              # PG Queue Balancer (~91)
standalone_discovery/tests/         # discovery (~185)
```

**Один вызов (хост или скрипт):**

```bash
bash scripts/run_all_tests.sh
# эквивалент: pytest tests/ standalone_discovery/tests/ -v --tb=short
```

**Не считается достаточным:** `pytest tests/test_task_queue_postpone.py` без полного прогона.

---

## Правило SP — статусы в документации спринта

### Когда отмечать блок выполненным

Статус **✅ / ☑** в документации спринта агент ставит **только после явного подтверждения разработчиком**, что на dev-сервере (`vps-101`) прошёл **полный** pytest:

- `276 tests collected`, **0 errors** при collection;
- все ожидаемые тесты **passed** (допустимы documented `skipped`).

**Запрещено** отмечать блок готовым, если:

- код написан, но разработчик ещё не подтвердил прогон на сервере;
- прогнаны только точечные файлы;
- collection падает (как при отсутствии `httpx2` в образе).

### Какие документы обновлять

| Документ | Когда |
|----------|--------|
| [`docs(plan)/план-исполнения-s2-s3.md`](docs(plan)/план-исполнения-s2-s3.md) | Задачи текущей итерации S2→S3 (B5, C2, …) |
| [`docs(plan)/рассказ-спринты-s1-s2-s3.md`](docs(plan)/рассказ-спринты-s1-s2-s3.md) | Если задача фигурирует в текущем спринте / review |
| [`docs(plan)/план-исполнения.md`](docs(plan)/план-исполнения.md) | Закрытые задачи итерации S1 (исторический план) |

### Что менять в документе

1. Карточка задачи: `⬜` → **✅ ГОТОВА** + дата и среда проверки.
2. Таблицы фаз / приоритетов / граф зависимостей: `B5` → `B5✅`.
3. DoD итерации: отдельная строка `☑ B5 — pytest green (vps-101, YYYY-MM-DD)`.
4. Трассируемость §30: отметить критерии, которые закрывает задача.

### Формулировка в отчёте агента

После подтверждения разработчика одной строкой:

```text
Обновлена документация спринта: B5 ✅ в план-исполнения-s2-s3.md (vps-101, полный pytest, 2026-06-19).
```

---

## Dev-сервер и база данных

### Окружение

| Параметр | Значение |
|----------|----------|
| Dev-app сервер | `vps-101`, пользователь `ubuntu`, репозиторий `~/Lidogen_telegram_balancer` |
| Dev/staging БД | `lead_monitor` на `vps-100` (Tailscale) |
| DSN | `QUEUE_DATABASE_URL` в `~/Lidogen_telegram_balancer/.env` |
| Доступ к БД | Tailscale, тег `tag:app` → `tag:db:5432` |
| Документация | [`docs(plan)/db-access-via-tailscale.md`](docs(plan)/db-access-via-tailscale.md) |

Shared DB: `QUEUE_DATABASE_URL` = `DATABASE_URL` (очередь в той же PostgreSQL, что платформа).

### Подготовка на vps-101 (один раз)

```bash
cd ~/Lidogen_telegram_balancer
cp .env.example .env
# отредактировать .env: реальный пароль lead_monitor_owner, хост vps-100
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -r standalone_discovery/requirements.txt
```

---

## Команды: полные тесты на vps-101 (Linux)

Рабочий каталог — **всегда корень репозитория**, не `standalone_discovery/`:

```bash
cd ~/Lidogen_telegram_balancer
```

### Вариант 1 — pytest на хосте

```bash
cd ~/Lidogen_telegram_balancer
source .venv/bin/activate
set -a && source .env && set +a

# полный suite (~276 тестов)
bash scripts/run_all_tests.sh
```

С явным DSN без `.env`:

```bash
cd ~/Lidogen_telegram_balancer
source .venv/bin/activate
export QUEUE_DATABASE_URL='postgresql://lead_monitor_owner:ПАРОЛЬ@vps-100:5432/lead_monitor'
bash scripts/run_all_tests.sh
```

### Вариант 2 — Docker (рекомендуется на vps-101)

```bash
cd ~/Lidogen_telegram_balancer
docker compose build
docker compose run --rm test
```

Сервис `test` запускает **pytest напрямую** (`tests/` + `standalone_discovery/tests/`, ~276 тестов).

Локальная PG (без Tailscale):

```bash
cd ~/Lidogen_telegram_balancer
docker compose --profile local up -d postgres
docker compose --profile local run --rm migrate-local
docker compose --profile local run --rm test-local
```

### Вариант 3 — Makefile

```bash
cd ~/Lidogen_telegram_balancer
make docker-test          # test → vps-100 из .env
make docker-test-local    # postgres + migrate + test-local
```

### Миграции перед тестами (если менялась схема)

```bash
cd ~/Lidogen_telegram_balancer
set -a && source .env && set +a
docker compose run --rm migrate
# или на хосте (нужен psql):
./scripts/migrate_queue.sh
```

### Воркер очереди (ручная проверка, не pytest)

```bash
cd ~/Lidogen_telegram_balancer
set -a && source .env && set +a
python -m app_balance.queue_worker
```

---

## Команды: Windows (локальная разработка)

### Полный прогон (хост или Docker)

```powershell
cd c:\Работа\LidogenMicroservises\Lidogen_telegram_balancer
$env:QUEUE_DATABASE_URL = "postgresql://lead_monitor_owner:ПАРОЛЬ@vps-100:5432/lead_monitor"
bash scripts/run_all_tests.sh
```

### Локальная PG в Docker

```powershell
$env:QUEUE_DATABASE_URL = "postgresql://lead_monitor_owner:dev_password@localhost:5433/lead_monitor"
docker compose --profile local up -d postgres
docker compose --profile local run --rm migrate-local
docker compose --profile local run --rm test-local
```

---

## Интерпретация результата

| Результат | Действие агента |
|-----------|-----------------|
| Все ~276 passed (или ожидаемые skipped) | Можно отчитываться о готовности (остальные DoD — отдельно) |
| `SKIPPED` integration без `QUEUE_DATABASE_URL` | **Недостаточно** для задач с очередью/БД — прогнать с DSN |
| Любой `FAILED` | Исправить до отчёта «готово» |

---

## Структура тестов (ориентир)

```text
tests/                           # ~91, PG Queue Balancer
standalone_discovery/tests/      # ~185, discovery + SessionClump
scripts/run_all_tests.sh         # единая команда для хоста и Docker
```

Маркеры: [`pytest.ini`](pytest.ini) — `integration` для тестов с живой PostgreSQL.

---

## Чеклист перед «готово» (тестовая часть)

```text
☐ Unit-тесты для новой/изменённой логики добавлены или обновлены
☐ Integration-тесты для контракта с PG добавлены или обновлены (если затронут queue/db)
☐ Полный прогон: bash scripts/run_all_tests.sh или docker compose run --rm test
☐ В отчёте: passed/failed/skipped (~276 collected), DSN (vps-100 / local PG)
☐ Если integration пропущены — явная причина
☐ При add/remove/move файлов — обновлён раздел «Структура» в agents-rules.md
☐ При новых test-путях или deps — обновлены Dockerfile / docker-compose (правило D)
☐ В отчёте список файлов для SFTP + команды на vps-101 (правило SF), не git pull
☐ После подтверждения разработчиком полного pytest на vps-101 — статус ✅ в docs(plan) (правило SP)
```

---

## Связанные документы

- [`README.md`](README.md) — быстрый старт, Docker, pytest
- [`docs(plan)/план-исполнения-s2-s3.md`](docs(plan)/план-исполнения-s2-s3.md) — критерии приёмки по задачам
- [`docs(plan)/db-access-via-tailscale.md`](docs(plan)/db-access-via-tailscale.md) — доступ к dev-БД
