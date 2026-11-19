# Общий чат сотрудников

**Назначение.** Аутентифицированный общий чат OCC с историей, read state, ответами на конкретное сообщение и SSE-обновлениями.

**Код/API:** `app/modules/chat/`, `app/api/v1/chat.py`; `/api/v1/chat/summary`, `/messages`, `/read`, `/events`, delete route. Frontend: `/chat`.

**Хранение:** сообщения, reply-to relation, read marker и moderation data — PostgreSQL.

**Доступ:** пользователь с действующей OSS сессией; удаление сообщения дополнительно ограничено `chat.messages.delete` и backend RBAC. Точная видимость/модерация определяется endpoint policy.

**Ошибки и пределы:** SSE — канал обновления, PostgreSQL остаётся источником истины. При разрыве соединения UI должен загрузить историю заново. Не добавляйте персональные/биллинг-данные абонента в общий чат или логи.

## Модель и доставка

Rooms задают канал, messages хранят автора/текст и nullable `reply_to`, events поддерживают обновления, read rows — позицию пользователя. Reply содержит безопасный preview и не меняет исходное сообщение.

History пагинируется; send валидирует текст и reply target; `/read` сохраняет позицию; SSE может повторить событие, поэтому клиент дедуплицирует по ID и перечитывает историю после reconnect. Moderation delete сохраняет ссылочную целостность; HTML не считается доверенным.

| Маршрут | Семантика |
| --- | --- |
| `GET /summary` | unread/последнее состояние текущего пользователя |
| `GET/POST /messages` | страница истории / новое сообщение или reply |
| `POST /read` | persisted read marker |
| `GET /events` | SSE hint, не источник истины |
| `DELETE /messages/{id}` | RBAC moderation |

Проверки: reply на отсутствующее/недоступное сообщение, XSS text, pagination, reconnect, повтор SSE и удаление сообщения с ответами.
