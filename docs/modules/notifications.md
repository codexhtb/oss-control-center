# Уведомления

**Каналы.** Колокольчик OCC объединяет уведомления, связанные с внутренними поручениями и Field Work. Отдельный Field Work Telegram outbox отправляет privacy-safe личные уведомления подписанным получателям.

**Код/API:** `app/api/v1/notifications.py`, `/api/v1/tasks/notifications`, `app/modules/field_dispatch/notifications.py` и `personal_notifications.py`.

**Хранение:** событие и delivery состояния сохраняются в PostgreSQL. Чтение пользователя хранится отдельно; UI может обновлять список без reload. Телефон, адрес, лицевой счёт и техническое примечание выезда не должны попадать в личное сообщение уведомления.

**Доставка:** personal notification outbox обслуживает существующий Telegram poller после commit публикации/события. Идемпотентность предотвращает дубликаты; неоднозначный результат не повторяется бесконтрольно. Пользователю может требоваться предварительно открыть диалог с ботом.

**Доступ:** адресат и направления определяются сервером по активному account/installer и permission policy. Неактивные и неавторизованные получатели не уведомляются. Не запускать отдельный poller/`getUpdates` consumer.

## Каналы и идемпотентность

Колокольчик OCC читает PostgreSQL notifications и per-user `read_at`; frontend polling обновляет unread count без перезагрузки. Task notification использует тот же persisted state, поэтому оранжевый indicator исчезает после read и входа заново.

Telegram outbox создаётся в той же бизнес-транзакции, но отправляется после commit. Unique event/recipient key предотвращает дубль. Temporary failures и 429 получают bounded retry; blocked/unavailable private chat сохраняется как состояние подписки и не откатывает публикацию handoff. Уведомление не содержит PII абонента.

| Канал | Источник | Consumer |
| --- | --- | --- |
| OCC bell | notification row/read state | WEB polling |
| Internal Task | task notification | тот же UI state |
| Field Work personal Telegram | outbox + subscription | единственный poller |

Тесты должны доказывать commit-before-send, idempotency, read persistence, отсутствие уведомления неактивному/чужому направлению и сохранение business event при ошибке Telegram.
