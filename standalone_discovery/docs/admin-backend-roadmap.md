# План доработок бэкенда для админ-панели

Документ описывает реализацию API для админ-панели `standalone_discovery` (без фронта).

**Исключено:** audit-log (лог «кто что изменил») — не реализуется на бэкенде.

**Исключено:** авто-добавление в clump после QR — только ручной вызов `POST /parser/{id}/enroll-session`.

## Порядок фаз

1. Реестр всех аккаунтов (`account_store`, `GET /accounts/all`)
2. Блокировка и удаление аккаунтов
3. Per-account лимит каналов
4. Enroll-session (ручное подключение после QR)
5. Лимит добавлений в час
6. Очередь действий (bulk-операции)
7. Ребаланс заполненности + гистерезис

## Критерии готовности

- `GET /accounts/all` — все `.session` + runtime из clump
- Admin block исключает аккаунт из балансировки; delete удаляет файл и запись
- Per-account `max_channels` в `_pick_target`
- `add_channels_per_hour` — excess → pending
- `enroll-session` подключает аккаунт к clump вручную
- Bulk add через action queue (FIFO)
- Rebalance в idle-окне с гистерезисом

См. исходный план в `.cursor/plans/` для деталей по каждой фазе.
