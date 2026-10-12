# Личные Telegram-сессии кабинета

Дата: 2026-10-12.

Это отдельный контур от парсера. QR кабинета не вызывает `register_account_after_qr` и не пишет аккаунт в clump или очередь discovery.

## Где что лежит

| Что | Где |
|---|---|
| Код | `standalone_discovery/discovery_api/client_sessions/` |
| Файлы сессий | `CLIENT_SESSIONS_DIR`, не `SESSIONS_DIR` парсера |
| Учёт на сайте | `user_telegram_accounts`. Session string в БД сайта нет |
| Входящие лида | `POST` сайта `/api/internal/telegram/incoming` |
| Уведомления | существующий бот и `notification_deliveries`, не личная сессия |

## Переменные балансировщика

| Переменная | Смысл |
|---|---|
| `CLIENT_SESSIONS_DIR` | Каталог файлов Telethon только для кабинета |
| `CLIENT_WEBHOOK_URL` | Полный URL `/api/internal/telegram/incoming` |
| `CLIENT_WEBHOOK_SECRET` | HMAC тела, заголовок `X-Telegram-Signature` |
| `CLIENT_WEBHOOK_INTERNAL_SECRET` | Тот же секрет, что `INTERNAL_API_SECRET` сайта, если нужен запасной заголовок |
| `CLIENT_QR_TTL_SECONDS` | Сколько ждать QR, по умолчанию 300 |
| `CLIENT_LISTENER_POLL_SECONDS` | Пауза дозагрузки диалогов, по умолчанию 8 |

`API_ID`, `API_HASH` и `API_KEY` те же, что у Discovery API. Без `CLIENT_SESSIONS_DIR` listener на машине разработчика не создаёт каталог `/app`.

## Методы

Префикс `/discovery-api/client-sessions`, заголовок `X-API-Key`.

| Метод | Путь | Результат |
|---|---|---|
| POST | `/qr` | QR. В ответе нет `session_string` |
| GET | `/qr/{id}` | `pending`, `password_required`, `success`, `expired`, `error`, `canceled` |
| POST | `/qr/{id}/2fa` | Пароль уходит в Telethon и не пишется на диск |
| DELETE | `/qr/{id}` | Отмена QR |
| DELETE | `/{remote_account_id}` | Отзыв сессии и удаление файла |
| POST | `/{remote_account_id}/messages` | Личное сообщение. Повтор того же `idempotency_key` не шлёт ещё раз |
| GET | `/{remote_account_id}/history` | Страница истории, сначала 50 сообщений |
| POST | `/{remote_account_id}/watch` | Слушать только этот peer |

Listener после исходящего сообщения или открытия чата читает только подписанные диалоги. На рестарте курсор берётся из метаданных аккаунта. Чужие личные чаты на сайт не отправляются.

`FloodWait`, закрытая личка и недоступный peer возвращаются как `error_code` и не крутятся в цикле.

## Что не включать этим шагом

Прод-listener и workflow доставки бота включаются отдельно, после проверки секретов и лимита `NOTIFICATION_DELIVERY_GLOBAL_PER_MINUTE`.
