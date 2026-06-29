# n8n workflow — интеграция с Discovery API

Workflow, которые **вызывают или работают** с Lidogen Discovery API (`discovery-api/*`).

**Всего:** 14 из 72 workflow.  
**Источник:** выделено из [workflows-brief-desc.md](./workflows-brief-desc.md) (экспорт с `https://mokuegopasan.beget.app`).

## Хосты Discovery API

| Хост | Назначение |
|------|------------|
| `194.156.117.160:8100` | Production TG Discovery API |
| `vps-108.web.oboyma.ai` | Тестовый HTTPS (полный набор эндпойнтов) |
| `vps-106.web.oboyma.ai:8100` / `web.oboyma.ai:8100` | Тестовый health / parser/list |

## Миграция на новый prod-API (очередь)

Все 14 discovery-workflow переведены на новый prod-URL с обязательным заголовком `X-API-Key`.
Оригиналы **не изменялись** — для каждого создана копия-файл `*-newapi.json` (внутри `name` дополнен суффиксом ` (новый prod API)`), правки внесены только в копию.

- **Новый базовый URL:** `https://lidogen-balancer-tg-prod.web.oboyma.ai` (заменяет `194.156.117.160:8100`, `vps-108/vps-106.web.oboyma.ai`).
- **Авторизация:** переиспользован существующий credential `httpHeaderAuth` «Авторизация телеграм поиск» (id `5okNCR8zaFTeWUrt`). Если ключ нового сервера отличается — обновить значение credential в n8n вручную.
- **Очередь:** для `parser/{id}/add-channels` и `remove-channels` оставлен асинхронный режим (явно `?async=true`) — задачи кладутся в PG-очередь сервиса. Режим fire-and-forget, без поллинга статуса задач.
- **Перенаправление вызывающих workflow:** автоматизировано скриптом `switch_caller_workflows.py` (см. ниже). Активация копий и совпадающие webhook-пути по-прежнему — отдельный ручной шаг после ревью.

## Переключение вызывающих workflow (`switch_caller_workflows.py`)

Узлы `Execute Workflow` в вызывающих workflow ссылаются на discovery sub-workflow по серверному ID (`parameters.workflowId.value`). Скрипт `n8n/switch_caller_workflows.py` перенаправляет эти ссылки между оригиналами и `*-newapi` копиями, делает бэкап исходных файлов и заливает изменения в n8n.

**Предусловие:** копии `*-newapi.json` уже залиты в n8n (`python n8n/upload_n8n_newapi_workflows.py --update`), иначе имена `… (новый prod API)` не резолвятся и скрипт остановится с ошибкой, не тронув файлы.

### Прямой и обратный путь

- **Прямой** (на новый prod API): `python n8n/switch_caller_workflows.py --to-new`
- **Обратный** (откат на оригиналы): `python n8n/switch_caller_workflows.py --to-old`
- Предпросмотр без записи: добавить `--dry-run`.
- Только локальный патч + бэкап, без PUT в n8n: `--no-upload` (резолв ID newapi всё равно идёт через API, read).
- Активировать вызывающий после заливки: `--activate`.

Скрипт идемпотентен: повторный запуск в том же направлении даёт `skip` по всем файлам.

### Автопубликация референсов

n8n валидирует всю цепочку зависимостей при публикации: вызывающий workflow нельзя опубликовать (`PUT /workflows/{id}`), пока все sub-workflow из его узлов Execute Workflow не опубликованы. Скрипт делает это автоматически:

- `--to-new` — публикует целевые `(новый prod API)` workflow (`POST /workflows/{id}/activate`) **до** PUT вызывающих.
- `--to-old` — после успешного отката вызывающих снимает публикацию этих копий (`POST /workflows/{id}/deactivate`). Если при откате были ошибки, снятие публикации пропускается (чтобы не оставить опубликованный вызывающий со ссылкой на снятую копию).

В `--dry-run` действия publish/unpublish только печатаются; в `--no-upload` — пропускаются.

### Вызывающие workflow → цели (5 файлов → 3 цели)

| Вызывающий файл | Узлов | Цель (оригинал ID) | newapi-имя |
|-----------------|-------|--------------------|------------|
| `мониторим-новые-лиды-rfwZP2B8DuZy.json` | 1 | `RyoJ2daiN7LB2lUT` отправка сообщений из очереди | отправка сообщений из очереди (новый prod API) |
| `обработка-сообщений-из-source_messages-aV0SMUfgejD9.json` | 2 | `RyoJ2daiN7LB2lUT` отправка сообщений из очереди | отправка сообщений из очереди (новый prod API) |
| `monitor-payments-expirations-DBRjzEwv28nB.json` | 1 | `Ww3Hhp19xo2ymA3p` отправка уведомления | отправка уведомления (новый prod API) |
| `поиск-по-направлению-C3ZX5ZdFqhqH.json` | 1 | `Cno7xg0nQg8DxpB2` Телеграм поиск | Телеграм поиск (новый prod API) |
| `поиск-по-направлению-тг-2ObVjDauzM2Z.json` | 1 | `Cno7xg0nQg8DxpB2` Телеграм поиск | Телеграм поиск (новый prod API) |

Вне скоупа: `u0lLfinbjX4L` «отправка сообщения ТГ Бот» и `IiV06ftOjux7` «Создать слушатель телеграм» — в экспорте их не вызывает ни один workflow.

### Бэкапы

Перед перезаписью каждый изменяемый вызывающий файл копируется в `n8n/backups/callers/<timestamp>/`. Основной обратный путь — флаг `--to-old` (не зависит от бэкапов); бэкапы — дополнительная защитная копия исходного содержимого.

| № | Оригинал (файл) | Копия (`-newapi.json`) | Что изменено | Статус |
|---|------------------|------------------------|--------------|--------|
| 5 | `lidogen-discovery-vps-108-https-2GmwA9pkiGgu.json` | `lidogen-discovery-vps-108-https-2GmwA9pkiGgu-newapi.json` | baseUrl Config + примечание | копия создана |
| 16 | `telegram-clump-N76MbYCibCwa.json` | `telegram-clump-N76MbYCibCwa-newapi.json` | host (health, parser/list) | копия создана |
| 18 | `tg-parser-sync-loU7yRxMHvBq.json` | `tg-parser-sync-loU7yRxMHvBq-newapi.json` | host + `?async=true` (add/remove) | копия создана |
| 21 | `vk-parser-sync-YU1b2Ze7pi1Q.json` | `vk-parser-sync-YU1b2Ze7pi1Q-newapi.json` | host + `?async=true` (add/remove) | копия создана |
| 28 | `добавление-по-ссылке-тг-0oNNOhP8DcOP.json` | `добавление-по-ссылке-тг-0oNNOhP8DcOP-newapi.json` | host (add-channel-by-link) | копия создана |
| 36 | `новый-тг-парсер-эндпойнты-4xVsFkW1pH1O.json` | `новый-тг-парсер-эндпойнты-4xVsFkW1pH1O-newapi.json` | baseUrl Config | копия создана |
| 46 | `отправка-сообщений-из-очереди-RyoJ2daiN7LB.json` | `отправка-сообщений-из-очереди-RyoJ2daiN7LB-newapi.json` | host (bot/send-message) | копия создана |
| 47 | `отправка-сообщения-тг-бот-u0lLfinbjX4L.json` | `отправка-сообщения-тг-бот-u0lLfinbjX4L-newapi.json` | host (bot/send-message) | копия создана |
| 48 | `отправка-уведомления-Ww3Hhp19xo2y.json` | `отправка-уведомления-Ww3Hhp19xo2y-newapi.json` | host (bot/send-message) | копия создана |
| 57 | `принимаем-сообщения-…-start-в-тг-боте-D9rAabNCbTtH.json` | `принимаем-сообщения-…-start-в-тг-боте-D9rAabNCbTtH-newapi.json` | host (bot/send-message) | копия создана |
| 60 | `создать-слушатель-телеграм-IiV06ftOjux7.json` | `создать-слушатель-телеграм-IiV06ftOjux7-newapi.json` | host (parser list/start/delete) | копия создана |
| 63 | `телеграм-поиск-Cno7xg0nQg8D.json` | `телеграм-поиск-Cno7xg0nQg8D-newapi.json` | host (discover, add-channel, auth/qr) | копия создана |
| 71 | `эндпойнты-clumps-новый-сервер-3Tdw4BDO0eLz.json` | `эндпойнты-clumps-новый-сервер-3Tdw4BDO0eLz-newapi.json` | host (auth/qr) | копия создана |
| 72 | `эндпойнты-тг-парсер-S5KjfoTGkare.json` | `эндпойнты-тг-парсер-S5KjfoTGkare-newapi.json` | host (auth/qr, parser/*) | копия создана |

## Сводная таблица

| № | Workflow | ID | Запуск | Роль | Эндпойнты |
|---|----------|-----|--------|------|-----------|
| 5 | Lidogen Discovery — vps-108 (HTTPS) | `2GmwA9pkiGgu` | manual | тест | `discovery-api/*` (полный набор) |
| 16 | telegram clump | `N76MbYCibCwa` | без триггера | тест | health, parser/list |
| 18 | tg-parser-sync | `loU7yRxMHvBq` | schedule (1 мин) | **prod** — синхронизация TG-парсеров | parser/* |
| 21 | vk-parser-sync (комбинированный) | `YU1b2Ze7pi1Q` | schedule (1 мин) | **prod** — синхронизация VK через Discovery | parser/* |
| 28 | Добавление по ссылке ТГ | `0oNNOhP8DcOP` | webhook | **prod** — резолв TG-канала | add-channel-by-link |
| 36 | Новый ТГ парсер эндпойнты | `4xVsFkW1pH1O` | manual | тест | `discovery-api/*` |
| 46 | отправка сообщений из очереди | `RyoJ2daiN7LB` | sub-workflow | **prod** — уведомления в TG | bot/send-message |
| 47 | отправка сообщения ТГ Бот | `u0lLfinbjX4L` | sub-workflow | отправка TG-сообщения | bot/send-message |
| 48 | отправка уведомления | `Ww3Hhp19xo2y` | sub-workflow | **prod** — payment-уведомления | bot/send-message |
| 57 | Принимаем … start в ТГ боте | `D9rAabNCbTtH` | webhook | **prod** — онбординг /start | bot/send-message |
| 60 | Создать слушатель телеграм | `IiV06ftOjux7` | sub-workflow | старт Discovery-парсера | parser/list, start |
| 63 | Телеграм поиск | `Cno7xg0nQg8D` | sub-workflow | **prod** — поиск каналов | discover, discover-groups, add-channel-by-link, auth/qr |
| 71 | Эндпойнты clumps новый сервер | `3Tdw4BDO0eLz` | manual | тест QR-авторизации | auth/qr |
| 72 | Эндпойнты ТГ парсер | `S5KjfoTGkare` | без триггера | тест parser/auth | auth/qr, parser/* |

## Группы по назначению

### Production — парсеры и каналы

- **tg-parser-sync** — минутная синхронизация TG-каналов из PostgreSQL с Discovery-парсером (add/remove channels, start/stop).
- **vk-parser-sync (комбинированный)** — минутная синхронизация VK-каналов через Discovery API + VK user-listener.
- **Добавление по ссылке ТГ** — webhook `/tg-resolve-score`, add-channel-by-link → upsert в `source_channels`.
- **Создать слушатель телеграм** — list/start парсера для канала из `source_channels`.
- **Телеграм поиск** — discover / discover-groups / add-channel-by-link по seed-фразам; вызывается из «Поиск по направлению» и «Поиск по направлению ТГ».

### Production — Telegram Bot (bot/send-message)

- **отправка сообщений из очереди** — уведомления из `in_app_notifications` (VK или TG через Discovery bot).
- **отправка уведомления** — payment/системные уведомления; вызывается из Monitor payments expirations.
- **Принимаем … start в ТГ боте** — welcome-сообщение после `/start` + bind контакта.

### Sub-workflow без входящих ссылок в экспорте

- **отправка сообщения ТГ Бот** — обёртка над `bot/send-message`.

### Тестовые / clump (manual или без триггера)

- **Lidogen Discovery — vps-108 (HTTPS)**
- **telegram clump**
- **Новый ТГ парсер эндпойнты**
- **Эндпойнты clumps новый сервер**
- **Эндпойнты ТГ парсер**

## Цепочки с Discovery API

```
Chron → Поиск по направлениям общий → … → Телеграм поиск → Discovery API (discover, add-channel-by-link)
tg-parser-sync (cron) → Discovery API parser/*
Monitor payments expirations → отправка уведомления → bot/send-message
/recieve_message_start_tg → bot/send-message
Общий вебхук для добавления по ссылке → Добавление по ссылке ТГ → add-channel-by-link
```

---

## 5. Lidogen Discovery — vps-108 (HTTPS)

- **Файл:** `lidogen-discovery-vps-108-https-2GmwA9pkiGgu.json`
- **ID:** `2GmwA9pkiGgu`
- **Тип запуска:** manual
- **Что делает:** Ручной тестовый свитчер HTTP-эндпойнтов Discovery API на vps-108 (HTTPS): парсеры, QR-авторизация, discover, bot/send-message, health.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - vps-108.web.oboyma.ai/discovery-api/* (add-channel-by-link, auth/qr, discover, discover-groups, parser/*, bot/send-message, health)

---

## 16. telegram clump

- **Файл:** `telegram-clump-N76MbYCibCwa.json`
- **ID:** `N76MbYCibCwa`
- **Тип запуска:** без триггера
- **Что делает:** Фрагмент без триггера: проверка health и list парсеров Discovery API на vps-106/web.oboyma.ai — тестовый clump узлов.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - vps-106.web.oboyma.ai:8100/health
    - web.oboyma.ai:8100/discovery-api/parser/list

---

## 18. tg-parser-sync

- **Файл:** `tg-parser-sync-loU7yRxMHvBq.json`
- **ID:** `loU7yRxMHvBq`
- **Тип запуска:** schedule (каждую минуту)
- **Что делает:** Каждую минуту синхронизирует Telegram-каналы из PostgreSQL с Discovery-парсером: добавляет/удаляет каналы, стартует/стопит парсеры; webhook telegram-messages для входящих.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - 194.156.117.160:8100/discovery-api/parser/* (list, start, stop, add-channels, remove-channels)
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/telegram-messages
  - **PostgreSQL:**
    - source_channels (select новых каналов)

---

## 21. vk-parser-sync (комбинированный)

- **Файл:** `vk-parser-sync-YU1b2Ze7pi1Q.json`
- **ID:** `YU1b2Ze7pi1Q`
- **Тип запуска:** schedule (каждую минуту)
- **Что делает:** Комбинированная минутная синхронизация: VK-каналы через Discovery API (add/remove channels) и VK user-listener (start, walls/add, status).

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - 194.156.117.160:8100/discovery-api/parser/*
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/listeners/vk-user/{session}/*
  - **PostgreSQL:**
    - source_channels (select)

---

## 28. Добавление по ссылке ТГ

- **Файл:** `добавление-по-ссылке-тг-0oNNOhP8DcOP.json`
- **ID:** `0oNNOhP8DcOP`
- **Тип запуска:** webhook (/tg-resolve-score)
- **Что делает:** Webhook GET /tg-resolve-score: добавляет Telegram-канал по ссылке через Discovery API, upsert в source_channels, возвращает метаданные.

- **Вызывает workflow:** нет
- **Вызывается из workflow:**
  - Общий вебхук для добавления по ссылке
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - 194.156.117.160:8100/discovery-api/add-channel-by-link
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/tg-resolve-score
  - **PostgreSQL:**
    - source_channels (upsert)

---

## 36. Новый ТГ парсер эндпойнты

- **Файл:** `новый-тг-парсер-эндпойнты-4xVsFkW1pH1O.json`
- **ID:** `4xVsFkW1pH1O`
- **Тип запуска:** manual
- **Что делает:** Ручной тестовый свитчер всех основных Discovery API эндпойнтов Telegram-парсера (health, parser, auth/qr, discover, bot/send-message).

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - {baseUrl}/discovery-api/* (полный набор parser/auth/discover/bot эндпойнтов)

---

## 46. отправка сообщений из очереди

- **Файл:** `отправка-сообщений-из-очереди-RyoJ2daiN7LB.json`
- **ID:** `RyoJ2daiN7LB`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: читает in_app_notifications, подтягивает данные user/project/message/channel и отправляет уведомление в VK, TG (Discovery bot) или помечает прочитанным.

- **Вызывает workflow:** нет
- **Вызывается из workflow:**
  - Мониторим новые лиды
  - Обработка Сообщений из source_messages
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - 194.156.117.160:8100/discovery-api/bot/send-message
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/messages/vk/send
  - **Lidogen site (gragipemuse.beget.app):**
    - gragipemuse.beget.app/leads?leadId=...
  - **PostgreSQL:**
    - users
    - user_contact_channels
    - source_messages
    - source_channels
    - monitoring_projects
    - in_app_notifications

---

## 47. отправка сообщения ТГ Бот

- **Файл:** `отправка-сообщения-тг-бот-u0lLfinbjX4L.json`
- **ID:** `u0lLfinbjX4L`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: отправляет текстовое сообщение через Discovery API bot/send-message.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - 194.156.117.160:8100/discovery-api/bot/send-message

---

## 48. отправка уведомления

- **Файл:** `отправка-уведомления-Ww3Hhp19xo2y.json`
- **ID:** `Ww3Hhp19xo2y`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: отправляет payment/системное уведомление пользователю в VK или Telegram и обновляет статус in_app_notifications.

- **Вызывает workflow:** нет
- **Вызывается из workflow:**
  - Monitor payments expirations
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - 194.156.117.160:8100/discovery-api/bot/send-message
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/messages/vk/send
  - **PostgreSQL:**
    - in_app_notifications (update)

---

## 57. Принимаем сообщения о том что пользователь нажал start в ТГ боте

- **Файл:** `принимаем-сообщения-о-том-что-пользователь-нажал-start-в-тг-боте-D9rAabNCbTtH.json`
- **ID:** `D9rAabNCbTtH`
- **Тип запуска:** webhook (/recieve_message_start_tg)
- **Что делает:** Webhook POST /recieve_message_start_tg: обрабатывает /start в Telegram-боте — bind контакта, welcome через Discovery bot, уведомление на сайт.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - 194.156.117.160:8100/discovery-api/bot/send-message
  - **Lidogen site (gragipemuse.beget.app):**
    - gragipemuse.beget.app/api/internal/contact-bind/consume
    - gragipemuse.beget.app/notifications

---

## 60. Создать слушатель телеграм

- **Файл:** `создать-слушатель-телеграм-IiV06ftOjux7.json`
- **ID:** `IiV06ftOjux7`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: для Telegram-канала из source_channels находит/стартует Discovery-парсер и привязывает канал.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - 194.156.117.160:8100/discovery-api/parser/list|start|{parserId}
  - **PostgreSQL:**
    - source_channels (select)

---

## 63. Телеграм поиск

- **Файл:** `телеграм-поиск-Cno7xg0nQg8D.json`
- **ID:** `Cno7xg0nQg8D`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: по seed-фразам ищет Telegram-каналы/группы через Discovery API (discover, discover-groups, add-channel-by-link), upsert в source_channels.

- **Вызывает workflow:** нет
- **Вызывается из workflow:**
  - Поиск по направлению
  - Поиск по направлению ТГ
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - 194.156.117.160:8100/discovery-api/discover
    - discover-groups
    - add-channel-by-link
    - auth/qr
  - **Telegram:**
    - t.me
  - **PostgreSQL:**
    - source_channels (upsert, select)

---

## 71. Эндпойнты clumps новый сервер

- **Файл:** `эндпойнты-clumps-новый-сервер-3Tdw4BDO0eLz.json`
- **ID:** `3Tdw4BDO0eLz`
- **Тип запуска:** manual
- **Что делает:** Ручной тест QR-авторизации Telegram-сессии на vps-108 Discovery API и проверка каналов в source_channels.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - vps-108.web.oboyma.ai/discovery-api/auth/qr
    - auth/qr/{session_id}/status
  - **PostgreSQL:**
    - source_channels (select)

---

## 72. Эндпойнты ТГ парсер

- **Файл:** `эндпойнты-тг-парсер-S5KjfoTGkare.json`
- **ID:** `S5KjfoTGkare`
- **Тип запуска:** без триггера
- **Что делает:** Фрагмент без триггера: набор HTTP-узлов для тестирования Discovery parser/auth на 194.156.117.160:8100 и webhook telegram-messages.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Lidogen Discovery API:**
    - 194.156.117.160:8100/discovery-api/auth/qr
    - parser/list|start|stop|status
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/telegram-messages

---
