# Краткое описание n8n workflow

Всего workflow: **72**. Источник: экспорт с `https://mokuegopasan.beget.app`.

Для каждого workflow указаны: назначение, тип запуска, связи с другими workflow, внешние API.

> Связи «вызывается из» построены по узлам `Execute Workflow` в экспортированных JSON. Workflow, вызываемые только вручную или из неэкспортированных сценариев, могут не иметь входящих ссылок.

> **Discovery API:** 14 workflow работают с Lidogen Discovery API — вынесены в отдельный документ [workflows-discovery-api.md](./workflows-discovery-api.md). В описаниях ниже такие workflow помечены тегом `[Discovery API]`.
>
> **Миграция на новый prod-API:** для всех 14 discovery-workflow созданы копии `*-newapi.json` с переключением на `https://lidogen-balancer-tg-prod.web.oboyma.ai` (+ `X-API-Key`, очередь `?async=true`). Оригиналы не изменялись. Детали — в разделе «Миграция» в [workflows-discovery-api.md](./workflows-discovery-api.md). Под каждым мигрированным workflow указана пометка `→ мигрировано: <файл-копии>`.

## Основные цепочки (кратко)

| Цепочка | Workflow |
|---------|----------|
| **Поиск лидов по расписанию** | Chron → Поиск по направлениям общий → Поиск по направлению / ТГ → RunSeedGeneration + Телеграм поиск + VK search-with-score |
| **Обработка комментариев → лиды** | VK_entrypoint / comment-reciever → channel_messages → L (цикл клиентов) → Обработка очереди. Поиск Лидов |
| **Входящие сообщения → AI** | telegram-messages / source_messages → Запуск обработки сообщений → Обработка Сообщений из source_messages → отправка из очереди |
| **Синхронизация парсеров** | tg-parser-sync, vk-parser-sync (минутные cron) |
| **Онбординг бота** | /recieve_message_start_tg, /vk_callback → Lidogen site contact-bind |
| **Подписки** | Monitor payments expirations → отправка уведомления |

**Ключевые внешние сервисы:** PostgreSQL (основная БД), OpenRouter (LLM), `159.194.221.16:8000/classify` (ML-классификатор), `217.26.24.119:3000` (VK omni-parser), `217.26.24.119:8000` (VK scoring), `194.156.117.160:8100` (Discovery API TG), `gragipemuse.beget.app` (сайт Lidogen).

---

## 1. Callback_vk_test

- **Файл:** `callback_vk_test-wvl1yXoTIsoL.json`
- **ID:** `wvl1yXoTIsoL`
- **Тип запуска:** webhook
- **Что делает:** Тестовый VK Callback API: принимает GET-запрос подтверждения сообщества (type=confirmation) и отвечает строкой подтверждения.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - нет

---

## 2. Chron-собрать данные по промптам и определить направления

- **Файл:** `chron-собрать-данные-по-промптам-и-определить-направления-zvaHc686EYmd.json`
- **ID:** `zvaHc686EYmd`
- **Тип запуска:** schedule (раз в неделю)
- **Что делает:** Раз в неделю читает активные проекты из БД, LLM-агентом группирует промпты по направлениям и запускает общий поиск по каждому направлению.

- **Вызывает workflow:**
  - Поиск по направлениям общий
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **PostgreSQL:**
    - monitoring_projects (select)
  - **LLM (OpenRouter):**
    - OpenRouter (z-ai/glm-4.7-flash)

---

## 3. L

- **Файл:** `l-EuysCucseBd5.json`
- **ID:** `EuysCucseBd5`
- **Тип запуска:** без триггера
- **Что делает:** Пустой черновик workflow без узлов — заготовка, не реализует бизнес-процесс.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - нет

---

## 4. L В цикле запускаем обработку каждого клиента

- **Файл:** `l-в-цикле-запускаем-обработку-каждого-клиента-6s6bsnEb1XF4.json`
- **ID:** `6s6bsnEb1XF4`
- **Тип запуска:** schedule (каждые 10 сек)
- **Что делает:** Каждые 10 секунд перебирает клиентов из Data Table «Клиенты» и для каждого запускает обработку очереди поиска лидов.

- **Вызывает workflow:**
  - Обработка очереди. Поиск Лидов
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **n8n Data Tables:**
    - Клиенты

---

## 5. Lidogen Discovery — vps-108 (HTTPS) `[Discovery API]`

> → мигрировано: `lidogen-discovery-vps-108-https-2GmwA9pkiGgu-newapi.json`

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

## 6. Lidogen VK — поиск и добавление по ссылкам

- **Файл:** `lidogen-vk-поиск-и-добавление-по-ссылкам-uxvHrHncQjFG.json`
- **ID:** `uxvHrHncQjFG`
- **Тип запуска:** webhook (/vk/search, /vk/resolve, /vk/add-by-link)
- **Что делает:** Production-эндпойнты VK: POST /vk/search, /vk/resolve, /vk/add-by-link — поиск групп, резолв ссылки, скоринг и upsert в source_channels.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **VK scoring/search service:**
    - 217.26.24.119:8000/groups/search
    - 217.26.24.119:8000/groups/resolve
    - 217.26.24.119:8000/groups/{id}/score
  - **PostgreSQL:**
    - source_channels (upsert)

---

## 7. Monitor payments expirations

- **Файл:** `monitor-payments-expirations-DBRjzEwv28nB.json`
- **ID:** `DBRjzEwv28nB`
- **Тип запуска:** schedule (ежедневно 8:00)
- **Что делает:** Ежедневно в 8:00 находит проекты с истекающей оплатой, собирает контакты владельцев и отправляет им уведомления об истечении подписки.

- **Вызывает workflow:**
  - отправка уведомления
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **PostgreSQL:**
    - monitoring_projects
    - user_contact_channels
    - in_app_notifications

---

## 8. My workflow 2

- **Файл:** `my-workflow-2-s1cKaoxOfWRs.json`
- **ID:** `s1cKaoxOfWRs`
- **Тип запуска:** manual
- **Что делает:** Ручной тест VK API: запрос wall.getCommentsForPosts для получения комментариев под постами.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **VK API (api.vk.com):**
    - api.vk.com/method/wall.getCommentsForPosts

---

## 9. My workflow 3

- **Файл:** `my-workflow-3-FNxUvj23SQ2U.json`
- **ID:** `FNxUvj23SQ2U`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: отправка тестового VK-сообщения с inline-кнопками через omni-parser messages/send.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/vk/user/{session}/messages/send

---

## 10. My workflow 4

- **Файл:** `my-workflow-4-g4jiARKkY3Vu.json`
- **ID:** `g4jiARKkY3Vu`
- **Тип запуска:** manual
- **Что делает:** Пустой ручной тест: один HTTP Request без настроенного URL — черновик для экспериментов.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - нет

---

## 11. My workflow 5

- **Файл:** `my-workflow-5-BCqDY5gSloPq.json`
- **ID:** `BCqDY5gSloPq`
- **Тип запуска:** manual
- **Что делает:** Пустой ручной тест: один HTTP Request без настроенного URL — черновик для экспериментов.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - нет

---

## 12. My workflow 6

- **Файл:** `my-workflow-6-4v0ajwC8J0BL.json`
- **ID:** `4v0ajwC8J0BL`
- **Тип запуска:** manual
- **Что делает:** Ручной тест VK user-listener API: завершение OAuth-авторизации клиента, остановка слушателя, пробный POST в webhook comment-reciever.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/clients/vk-user/auth/complete
    - 217.26.24.119:3000/listeners/vk-user/{session}/stop
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/comment-reciever
  - **VK OAuth:**
    - oauth.vk.com/blank.html (страница получения токена)

---

## 13. My workflow 7

- **Файл:** `my-workflow-7-eRBHcSoU5Fa9.json`
- **ID:** `eRBHcSoU5Fa9`
- **Тип запуска:** manual
- **Что делает:** Ручной тест Instagram API: обмен short-lived access token на long-lived через кастомный Instagram-узел.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Instagram Graph API:**
    - Instagram Graph — exchangeAccessToken

---

## 14. My workflow

- **Файл:** `my-workflow-8FH9hNV5xAns.json`
- **ID:** `8FH9hNV5xAns`
- **Тип запуска:** manual
- **Что делает:** Ручной тест ML-классификатора: перебирает записи Data Table «Результат Фильтра 2» и отправляет их на /classify.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 159.194.221.16:8000/classify
  - **n8n Data Tables:**
    - Результат Фильтра 2

---

## 15. RunSeedGeneration

- **Файл:** `runseedgeneration-ejj4SsLhqLbG.json`
- **ID:** `ejj4SsLhqLbG`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: LLM-агент генерирует ~100 поисковых сидов (seed-фраз) для Telegram/VK по описанию ниши, парсит теги <seed> и сохраняет в PostgreSQL.

- **Вызывает workflow:** нет
- **Вызывается из workflow:**
  - Поиск по направлению
  - Поиск по направлению ТГ
- **Сторонние API / интеграции:**
  - **LLM (OpenRouter):**
    - OpenRouter (openai/gpt-oss-120b)
  - **PostgreSQL:**
    - executeQuery — сохранение сидов

---

## 16. telegram clump `[Discovery API]`

> → мигрировано: `telegram-clump-N76MbYCibCwa-newapi.json`

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

## 17. test

- **Файл:** `test-YKkxGkQVZwWe.json`
- **ID:** `YKkxGkQVZwWe`
- **Тип запуска:** webhook
- **Что делает:** Минимальный тестовый webhook: принимает GET и возвращает set-данные через Respond to Webhook.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - нет

---

## 18. tg-parser-sync `[Discovery API]`

> → мигрировано: `tg-parser-sync-loU7yRxMHvBq-newapi.json` (add/remove-channels через очередь `?async=true`)

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

## 19. TikTok_entrypoint

- **Файл:** `tiktok_entrypoint-hvSIO0WCkO82.json`
- **ID:** `hvSIO0WCkO82`
- **Тип запуска:** webhook (/TikTok-entrypoint)
- **Что делает:** Webhook-заглушка TikTok entrypoint (POST /TikTok-entrypoint): принимает payload и нормализует через Set — без дальнейшей обработки.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - нет

---

## 20. vk-parser-sync

- **Файл:** `vk-parser-sync-fQEfGT5fec6h.json`
- **ID:** `fQEfGT5fec6h`
- **Тип запуска:** schedule (каждую минуту)
- **Что делает:** Каждую минуту синхронизирует VK-группы из БД с VK user-listener: добавляет/удаляет walls для мониторинга комментариев, проверяет status.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/listeners/vk-user/{session}/comments/walls/add|remove|status
  - **PostgreSQL:**
    - source_channels (select новых каналов)

---

## 21. vk-parser-sync (комбинированный) `[Discovery API]`

> → мигрировано: `vk-parser-sync-YU1b2Ze7pi1Q-newapi.json` (add/remove-channels через очередь `?async=true`)

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

## 22. VK добпавление по ссылке

- **Файл:** `vk-добпавление-по-ссылке-oaXx0WiiHjCS.json`
- **ID:** `oaXx0WiiHjCS`
- **Тип запуска:** webhook (/vk-resolve-score)
- **Что делает:** Webhook GET /vk-resolve-score: резолвит VK-ссылку, считает score группы, upsert в source_channels; возвращает полные или сокращённые данные.

- **Вызывает workflow:** нет
- **Вызывается из workflow:**
  - Общий вебхук для добавления по ссылке
- **Сторонние API / интеграции:**
  - **VK scoring/search service:**
    - {apiBase}/groups/resolve
    - {apiBase}/groups/{vk_id}/score
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/vk-resolve-score
  - **PostgreSQL:**
    - source_channels (upsert)

---

## 23. VK поиск и запись с метаданными и скором

- **Файл:** `vk-поиск-и-запись-с-метаданными-и-скором-ikGTpQHNxjD8.json`
- **ID:** `ikGTpQHNxjD8`
- **Тип запуска:** webhook (/vk/search-with-score)
- **Что делает:** Webhook POST /vk/search-with-score: ищет VK-группы по запросу, записывает с метаданными и скором в PostgreSQL (upsert/insert).

- **Вызывает workflow:** нет
- **Вызывается из workflow:**
  - Поиск по направлению
- **Сторонние API / интеграции:**
  - **VK scoring/search service:**
    - 217.26.24.119:8000/groups/search
    - 217.26.26.32:8000/groups/search
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/vk/search-with-score
  - **PostgreSQL:**
    - source_channels (upsert, insert)

---

## 24. VK — поиск (через webhook)(Без метаданных и скора)

- **Файл:** `vk-поиск-через-webhook-без-метаданных-и-скора-d0zQoirLtg7A.json`
- **ID:** `d0zQoirLtg7A`
- **Тип запуска:** webhook (/vk/search)
- **Что делает:** Webhook POST /vk/search: упрощённый поиск VK-групп без скоринга, upsert результатов в source_channels.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **VK scoring/search service:**
    - 217.26.24.119:8000/groups/search
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/vk/search
  - **PostgreSQL:**
    - source_channels (upsert)

---

## 25. VK_entrypoint

- **Файл:** `vk_entrypoint-ExMuCtt8Pr7n.json`
- **ID:** `ExMuCtt8Pr7n`
- **Тип запуска:** webhook (/vk-entrypoint)
- **Что делает:** Webhook POST /vk-entrypoint: принимает события от VK user-listener (wall_comment и др.), фильтрует и записывает в source_messages / проксирует в comment-reciever.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/comment-reciever
  - **PostgreSQL:**
    - source_messages (insert)

---

## 26. Вебхук принимаем изменения в message_ai_screening

- **Файл:** `вебхук-принимаем-изменения-в-message_ai_screening-Gjbc9tJxs9Go.json`
- **ID:** `Gjbc9tJxs9Go`
- **Тип запуска:** webhook (/ai-runs-reciever)
- **Что делает:** Webhook GET /ai-runs-reciever — заглушка для приёма изменений AI-screening runs (только узел Webhook, без downstream-логики).

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - нет

---

## 27. Включаем слушатель группы

- **Файл:** `включаем-слушатель-группы-TzVNnyflmLD5.json`
- **ID:** `TzVNnyflmLD5`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: стартует VK user-listener (Server_account_2) для мониторинга группы через POST /listeners/vk-user/{session}/start.

- **Вызывает workflow:** нет
- **Вызывается из workflow:**
  - Запуск мониторинга групп
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/listeners/vk-user/{session}/start

---

## 28. Добавление по ссылке ТГ `[Discovery API]`

> → мигрировано: `добавление-по-ссылке-тг-0oNNOhP8DcOP-newapi.json`

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

## 29. Дописать в таблицу найденные группы

- **Файл:** `дописать-в-таблицу-найденные-группы-B7N6Q2kwnPz5.json`
- **ID:** `B7N6Q2kwnPz5`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: обогащает найденные VK-группы через groups.getById и записывает в Data Table «Группы клиента».

- **Вызывает workflow:** нет
- **Вызывается из workflow:**
  - Поиск по set
- **Сторонние API / интеграции:**
  - **VK API (api.vk.com):**
    - api.vk.com/method/groups.getById
  - **n8n Data Tables:**
    - Группы клиента
    - Поиск

---

## 30. Запуск мониторинга групп

- **Файл:** `запуск-мониторинга-групп-Cc0vkUubiJJg.json`
- **ID:** `Cc0vkUubiJJg`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: по client_id читает группы клиента из Data Table, для каждой включает VK-слушатель и рекурсивно запускает себя для следующих групп.

- **Вызывает workflow:**
  - Включаем слушатель группы
  - Запуск мониторинга групп
- **Вызывается из workflow:**
  - Запуск мониторинга групп
- **Сторонние API / интеграции:**
  - **n8n Data Tables:**
    - Группы клиента
    - Клиент->Вебхук

---

## 31. Запуск обработки сообщений

- **Файл:** `запуск-обработки-сообщений-FfXByAZO1oF2.json`
- **ID:** `FfXByAZO1oF2`
- **Тип запуска:** schedule (каждую минуту)
- **Что делает:** Каждую минуту выбирает необработанные записи source_messages из PostgreSQL и запускает workflow AI-обработки сообщений.

- **Вызывает workflow:**
  - Обработка Сообщений из source_messages
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **PostgreSQL:**
    - source_messages (executeQuery — выборка на обработку)

---

## 32. Зарегистрировать сообщество в API (вручную)

- **Файл:** `зарегистрировать-сообщество-в-api-вручную-yQNoJdzecQ4I.json`
- **ID:** `yQNoJdzecQ4I`
- **Тип запуска:** manual
- **Что делает:** Ручная регистрация VK-сообщества в omni-parser: очистка clients, регистрация vk-клиента и старт VK-listener.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/clients/clear
    - 217.26.24.119:3000/clients/vk
    - 217.26.24.119:3000/listeners/vk/start

---

## 33. Зарегистрировать центр уведомлений ВК

- **Файл:** `зарегистрировать-центр-уведомлений-вк-BZeaygtH4wDe.json`
- **ID:** `BZeaygtH4wDe`
- **Тип запуска:** webhook (/register_username_notification_centre)
- **Что делает:** Webhook POST /register_username_notification_centre: регистрирует VK-пользователя как канал уведомлений (user_notification_channels) по vk.com/id.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/register_username_notification_centre
  - **PostgreSQL:**
    - user_notification_channels (insert)

---

## 34. Мониторим новые лиды

- **Файл:** `мониторим-новые-лиды-rfwZP2B8DuZy.json`
- **ID:** `rfwZP2B8DuZy`
- **Тип запуска:** schedule (каждую минуту)
- **Что делает:** Раз в минуту ищет новые AI-screening runs со статусом lead и запускает отправку сообщений из очереди уведомлений.

- **Вызывает workflow:**
  - отправка сообщений из очереди
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **PostgreSQL:**
    - message_ai_screening_runs
    - in_app_notifications

---

## 35. Новая регистрация и мониторинг (в работе)

- **Файл:** `новая-регистрация-и-мониторинг-в-работе-DwwHiFt7GoLb.json`
- **ID:** `DwwHiFt7GoLb`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow (WIP): по параметрам клиента регистрирует VK-слушатель, добавляет посты для мониторинга комментариев через omni-parser API.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/listeners/vk-user/{client}/start
    - 217.26.24.119:3000/listeners/vk-user/{client}/comments/add
  - **n8n Data Tables:**
    - Клиент->Вебхук
    - Группы клиента

---

## 36. Новый ТГ парсер эндпойнты `[Discovery API]`

> → мигрировано: `новый-тг-парсер-эндпойнты-4xVsFkW1pH1O-newapi.json`

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

## 37. Обновить данные слушателя

- **Файл:** `обновить-данные-слушателя-MHgyxnT6ZHKN.json`
- **ID:** `MHgyxnT6ZHKN`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: перезапускает VK user-listener — stop → wait → start для указанной сессии; проверяет status.

- **Вызывает workflow:** нет
- **Вызывается из workflow:**
  - Обновляем посты для мониторинга
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/listeners/vk-user/{session}/status|stop|start

---

## 38. Обновляем посты для мониторинга

- **Файл:** `обновляем-посты-для-мониторинга-SVrI5HbHcOo1.json`
- **ID:** `SVrI5HbHcOo1`
- **Тип запуска:** schedule (раз в час / раз в 20 мин)
- **Что делает:** По расписанию (раз в час и раз в 20 мин): читает группы/каналы клиентов, обновляет список постов для мониторинга и перезапускает VK-слушатели.

- **Вызывает workflow:**
  - Обновить данные слушателя
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **PostgreSQL:**
    - source_channels (select)
  - **n8n Data Tables:**
    - Группы клиента
    - Клиент->Вебхук

---

## 39. Обработка очереди. Поиск Лидов

- **Файл:** `обработка-очереди-поиск-лидов-KGEiMOOXkIYk.json`
- **ID:** `KGEiMOOXkIYk`
- **Тип запуска:** sub-workflow
- **Что делает:** Главный pipeline поиска лидов для клиента: читает channel_messages по фильтрам, классифицирует (ML + LLM-агенты), отправляет ответы в VK/TG, при необходимости вызывает sub-workflow сообщений с кнопками.

- **Вызывает workflow:**
  - Сообщение в ВК опционально с кнопками
- **Вызывается из workflow:**
  - L В цикле запускаем обработку каждого клиента
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 159.194.221.16:8000/classify
    - 217.26.24.119:3000/vk/user/{session}/messages/send
  - **LLM (OpenRouter):**
    - OpenRouter (chat/completions)
  - **Telegram:**
    - n8n Telegram node — отправка в TG
  - **PostgreSQL:**
    - channel_messages (executeQuery/select)
  - **Google Forms (ссылки):**
    - docs.google.com/forms (ссылка в сообщениях)
  - **n8n Data Tables:**
    - Клиенты
    - Данные клиентов
    - Результат ответа
    - Результат Фильтра 2

---

## 40. Обработка Сообщений из source_messages

- **Файл:** `обработка-сообщений-из-source_messages-aV0SMUfgejD9.json`
- **ID:** `aV0SMUfgejD9`
- **Тип запуска:** sub-workflow
- **Что делает:** AI-обработка входящих source_messages: LLM-скрининг, запись message_ai_screening_runs, создание in_app_notifications и запуск отправки из очереди для лидов.

- **Вызывает workflow:**
  - отправка сообщений из очереди
- **Вызывается из workflow:**
  - Запуск обработки сообщений
- **Сторонние API / интеграции:**
  - **LLM (OpenRouter):**
    - OpenRouter (chat/completions)
  - **PostgreSQL:**
    - source_messages
    - message_ai_screening_runs
    - monitoring_projects
    - in_app_notifications

---

## 41. Общий вебхук для добавления по ссылке

- **Файл:** `общий-вебхук-для-добавления-по-ссылке-K6XZZtoEByt8.json`
- **ID:** `K6XZZtoEByt8`
- **Тип запуска:** webhook (/common_by_link)
- **Что делает:** Webhook GET /common_by_link: определяет тип ссылки (TG/VK) и проксирует в tg-resolve-score или vk-resolve-score.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/common_by_link
    - mokuegopasan.beget.app/webhook/tg-resolve-score
    - mokuegopasan.beget.app/webhook/vk-resolve-score
  - **Telegram:**
    - t.me (проверка ссылок)

---

## 42. Отключить слушатели

- **Файл:** `отключить-слушатели-ZguARVx0pxW0.json`
- **ID:** `ZguARVx0pxW0`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: останавливает VK user-listener (Server_account_2) через POST /listeners/vk-user/{session}/stop.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/listeners/vk-user/{session}/stop

---

## 43. Отправка в VK или телеграм

- **Файл:** `отправка-в-vk-или-телеграм-Edan2OUlje5w.json`
- **ID:** `Edan2OUlje5w`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: принимает text/buttons/user_id/group_id, собирает VK inline-keyboard и отправляет через messages/vk/send.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/messages/vk/send

---

## 44. отправка простого текстового сообщения

- **Файл:** `отправка-простого-текстового-сообщения-5lnVpGfm217N.json`
- **ID:** `5lnVpGfm217N`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: отправляет простое текстовое VK-сообщение через omni-parser messages/vk/send.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/messages/vk/send

---

## 45. Отправка сообщений ВК/ТЕЛЕГРАМ с кнопками и медиа (финал)

- **Файл:** `отправка-сообщений-вк-телеграм-с-кнопками-и-медиа-финал-ZwUEgzdZpzov.json`
- **ID:** `ZwUEgzdZpzov`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: универсальная отправка rich-сообщений — VK send или Telegram send_rich в зависимости от канала.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/messages/vk/send
    - 217.26.24.119:3000/messages/telegram/send_rich

---

## 46. отправка сообщений из очереди `[Discovery API]`

> → мигрировано: `отправка-сообщений-из-очереди-RyoJ2daiN7LB-newapi.json`

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

## 47. отправка сообщения ТГ Бот `[Discovery API]`

> → мигрировано: `отправка-сообщения-тг-бот-u0lLfinbjX4L-newapi.json`

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

## 48. отправка уведомления `[Discovery API]`

> → мигрировано: `отправка-уведомления-Ww3Hhp19xo2y-newapi.json`

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

## 49. Поиск из промптов

- **Файл:** `поиск-из-промптов-37rVWrJKd364.json`
- **ID:** `37rVWrJKd364`
- **Тип запуска:** manual
- **Что делает:** Ручной запуск: разбивает список промптов и для каждого вызывает «Поиск по направлению».

- **Вызывает workflow:**
  - Поиск по направлению
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - нет

---

## 50. Поиск по set

- **Файл:** `поиск-по-set-iAxgzkT9YJZI.json`
- **ID:** `iAxgzkT9YJZI`
- **Тип запуска:** manual
- **Что делает:** Ручной поиск VK-групп по заданному set запросов (groups.search + omni-parser search/groups), затем запись результатов через sub-workflow.

- **Вызывает workflow:**
  - Дописать в таблицу найденные группы
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **VK API (api.vk.com):**
    - api.vk.com/method/groups.search
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/vk/user/{session}/search/groups
  - **n8n Data Tables:**
    - Поиск
    - Группы клиента

---

## 51. Поиск по направлению

- **Файл:** `поиск-по-направлению-C3ZX5ZdFqhqH.json`
- **ID:** `C3ZX5ZdFqhqH`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: генерирует сиды (RunSeedGeneration), ищет VK-группы через webhook search-with-score и Telegram-каналы через «Телеграм поиск», upsert в source_channels.

- **Вызывает workflow:**
  - RunSeedGeneration
  - Телеграм поиск
- **Вызывается из workflow:**
  - Поиск из промптов
  - Поиск по направлениям общий
- **Сторонние API / интеграции:**
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/vk/search-with-score
  - **PostgreSQL:**
    - source_channels (select)

---

## 52. Поиск по направлению ТГ

- **Файл:** `поиск-по-направлению-тг-2ObVjDauzM2Z.json`
- **ID:** `2ObVjDauzM2Z`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: только Telegram-ветка — RunSeedGeneration + «Телеграм поиск» по описанию направления.

- **Вызывает workflow:**
  - RunSeedGeneration
  - Телеграм поиск
- **Вызывается из workflow:**
  - Поиск по направлениям общий
  - Поиск по промптам ТГ
- **Сторонние API / интеграции:**
  - нет

---

## 53. Поиск по направлениям общий

- **Файл:** `поиск-по-направлениям-общий-t3x1YhyaMXqA.json`
- **ID:** `t3x1YhyaMXqA`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow-оркестратор: для каждого направления запускает VK-поиск («Поиск по направлению») и TG-поиск («Поиск по направлению ТГ»).

- **Вызывает workflow:**
  - Поиск по направлению ТГ
  - Поиск по направлению
- **Вызывается из workflow:**
  - Chron-собрать данные по промптам и определить направления
- **Сторонние API / интеграции:**
  - нет

---

## 54. Поиск по промптам ТГ

- **Файл:** `поиск-по-промптам-тг-AFWu5Xq6sOoZ.json`
- **ID:** `AFWu5Xq6sOoZ`
- **Тип запуска:** manual
- **Что делает:** Ручной запуск Telegram-поиска: разбивает промпты и для каждого вызывает «Поиск по направлению ТГ».

- **Вызывает workflow:**
  - Поиск по направлению ТГ
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - нет

---

## 55. Прием комментариев под постами группы

- **Файл:** `прием-комментариев-под-постами-группы-xnqt0824z9Er.json`
- **ID:** `xnqt0824z9Er`
- **Тип запуска:** webhook (/comment-reciever)
- **Что делает:** Webhook POST /comment-reciever: принимает VK-комментарии, классифицирует (/classify), создаёт channel_messages и ставит в очередь обработки.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 159.194.221.16:8000/classify
    - 217.26.24.119:3000/clients/vk-user/{session}
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/comment-reciever
  - **Google Forms (ссылки):**
    - docs.google.com/forms (ссылка в ответах)
  - **PostgreSQL:**
    - source_messages
    - source_channels
    - channel_messages (insert/create)

---

## 56. Принимаем сообщение что пользователь нажал на старт в боте ВК

- **Файл:** `принимаем-сообщение-что-пользователь-нажал-на-старт-в-боте-вк-ERSWyrKTUvzn.json`
- **ID:** `ERSWyrKTUvzn`
- **Тип запуска:** webhook (/vk_callback)
- **Что делает:** Webhook POST /vk_callback: обрабатывает нажатие Start в VK-боте — bind контакта, welcome-сообщение, старт listener, уведомление на сайт.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **VK API (api.vk.com):**
    - api.vk.com/method/users.get
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/listeners/vk/start
    - 217.26.24.119:3000/messages/vk/send
  - **Lidogen site (gragipemuse.beget.app):**
    - gragipemuse.beget.app/api/internal/contact-bind/consume
    - gragipemuse.beget.app/notifications

---

## 57. Принимаем сообщения о том что пользователь нажал start в ТГ боте `[Discovery API]`

> → мигрировано: `принимаем-сообщения-о-том-что-пользователь-нажал-start-в-тг-боте-D9rAabNCbTtH-newapi.json`

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

## 58. Промты после прохождения опроса

- **Файл:** `промты-после-прохождения-опроса-irw14sVTJguA.json`
- **ID:** `irw14sVTJguA`
- **Тип запуска:** webhook (/project-new-data, /project-update-data)
- **Что делает:** Webhooks /project-new-data и /project-update-data: после опроса LLM-агентами генерирует/обновляет lead_search_prompt и поля monitoring_projects в PostgreSQL.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **LLM (OpenRouter):**
    - OpenRouter (2 агента)
  - **PostgreSQL:**
    - monitoring_projects (executeQuery, update)

---

## 59. регистрация каналов

- **Файл:** `регистрация-каналов-ZVPpskUjyfFd.json`
- **ID:** `ZVPpskUjyfFd`
- **Тип запуска:** manual
- **Что делает:** Ручная миграция: читает группы из Data Table «Группы клиента» и insert в PostgreSQL source_channels.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **n8n Data Tables:**
    - Группы клиента
  - **PostgreSQL:**
    - source_channels (insert)

---

## 60. Создать слушатель телеграм `[Discovery API]`

> → мигрировано: `создать-слушатель-телеграм-IiV06ftOjux7-newapi.json`

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

## 61. Сообщение в ВК опционально с кнопками

- **Файл:** `сообщение-в-вк-опционально-с-кнопками-NTfeVCens3Jh.json`
- **ID:** `NTfeVCens3Jh`
- **Тип запуска:** sub-workflow
- **Что делает:** Sub-workflow: формирует VK или Telegram rich-сообщение с опциональной inline-клавиатурой и отправляет через messages API.

- **Вызывает workflow:** нет
- **Вызывается из workflow:**
  - Обработка очереди. Поиск Лидов
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/messages/vk/send
    - 217.26.24.119:3000/messages/telegram/send_rich

---

## 62. Сообщения из сообщества лидогена

- **Файл:** `сообщения-из-сообщества-лидогена-5dJjDOY4fTus.json`
- **ID:** `5dJjDOY4fTus`
- **Тип запуска:** webhook (/community-paid-messages)
- **Что делает:** Webhook POST /community-paid-messages — заглушка для приёма платных сообщений из VK-сообщества Lidogen (без downstream-логики).

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - нет

---

## 63. Телеграм поиск `[Discovery API]`

> → мигрировано: `телеграм-поиск-Cno7xg0nQg8D-newapi.json`

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

## 64. Телеграм принимаем и записываем

- **Файл:** `телеграм-принимаем-и-записываем-NgUhSbXVyFBq.json`
- **ID:** `NgUhSbXVyFBq`
- **Тип запуска:** webhook (/telegram-messages)
- **Что делает:** Webhook POST /telegram-messages: принимает сообщения из TG-парсера, классифицирует, пишет source_messages и создаёт задачу в очереди.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 159.194.221.16:8000/classify
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/telegram-messages
  - **PostgreSQL:**
    - source_messages
    - source_channels (insert/select)

---

## 65. Тест получение комментариев

- **Файл:** `тест-получение-комментариев-hTh3CvCkwRW0.json`
- **ID:** `hTh3CvCkwRW0`
- **Тип запуска:** без триггера
- **Что делает:** Пустой черновик workflow без узлов — заготовка для теста получения VK-комментариев.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - нет

---

## 66. Тест Создаём сообщение

- **Файл:** `тест-создаём-сообщение-eTh5ndm300k1.json`
- **ID:** `eTh5ndm300k1`
- **Тип запуска:** manual
- **Что делает:** Ручной тест: отправляет фиктивный комментарий в webhook comment-reciever.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Webhook n8n (mokuegopasan.beget.app):**
    - mokuegopasan.beget.app/webhook/comment-reciever

---

## 67. Тест эндпойнты

- **Файл:** `тест-эндпойнты-EbX94BxYbtpW.json`
- **ID:** `EbX94BxYbtpW`
- **Тип запуска:** manual
- **Что делает:** Ручной тестовый свитчер omni-parser VK user/listener API: health, auth, search/groups, wall/posts, comments, board/topics.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 217.26.24.119:3000/health
    - 217.26.24.119:3000/status
    - 217.26.24.119:3000/clients/vk-user/*
    - 217.26.24.119:3000/listeners/vk-user/*
    - 217.26.24.119:3000/vk/user/*
  - **n8n Data Tables:**
    - Группы клиента

---

## 68. ТикТокТестВебхук

- **Файл:** `тиктоктествебхук-O0LDRYmL7lIB.json`
- **ID:** `O0LDRYmL7lIB`
- **Тип запуска:** webhook (/tiktoktest)
- **Что делает:** Тестовый webhook POST /tiktoktest — принимает payload без обработки.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - нет

---

## 69. ТикТокЭндпойнты

- **Файл:** `тиктокэндпойнты-cBMwZx9V7a4I.json`
- **ID:** `cBMwZx9V7a4I`
- **Тип запуска:** manual
- **Что делает:** Ручной тестовый свитчер TikTok microservice API: sessions, search, listeners, watches на 159.194.216.75:2000 и 217.26.24.119:2000.

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **Кастомные backend-сервисы:**
    - 159.194.216.75:2000/health|search|sessions|listeners|watches
    - 217.26.24.119:2000/health|search|sessions

---

## 70. Читаем старые сообщения

- **Файл:** `читаем-старые-сообщения-SS21fOFcrvtz.json`
- **ID:** `SS21fOFcrvtz`
- **Тип запуска:** schedule (раз в день)
- **Что делает:** Раз в день помечает старые непрочитанные in_app_notifications как прочитанные (cleanup).

- **Вызывает workflow:** нет
- **Вызывается из workflow:** не найдено в экспорте
- **Сторонние API / интеграции:**
  - **PostgreSQL:**
    - in_app_notifications (executeQuery select + update)

---

## 71. Эндпойнты clumps новый сервер `[Discovery API]`

> → мигрировано: `эндпойнты-clumps-новый-сервер-3Tdw4BDO0eLz-newapi.json`

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

## 72. Эндпойнты ТГ парсер `[Discovery API]`

> → мигрировано: `эндпойнты-тг-парсер-S5KjfoTGkare-newapi.json`

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
