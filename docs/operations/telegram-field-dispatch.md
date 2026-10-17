# Telegram Field Dispatch T1/T2 — dev LAB

T1 отправляет исходящие сообщения в фиксированную LAB-группу
`-5319791765`. Сохранение причины выезда создаёт `DRAFT`; сетевой запрос
выполняется только после отдельного нажатия «Отправить боту». T2 принимает
проверенные ответы на это сообщение и callback «✅ Выполнено». Автоматическое
закрытие и отправка в произвольные чаты отсутствуют.

## Секрет и Compose

Исходный токен хранится у владельца в
`/root/oss-secrets/telegram-bot-token` с правом `0600`. Значение не копируется
в Git, image, Compose environment или аргументы процесса. Override
`compose.telegram.dev.yaml` запускает одноразовый root init-контейнер: он
копирует токен в отдельный named volume как UID/GID `1001`, mode `0400`.
API получает этот volume read-only. Файл владельца не изменяется.

Все операции dev-стека выполняются с полным набором используемых override:

```bash
sg docker -c 'docker compose --env-file .env.dev -p oss-control-dev -f compose.dev.yaml -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml -f compose.telegram.dev.yaml config --quiet'
sg docker -c 'docker compose --env-file .env.dev -p oss-control-dev -f compose.dev.yaml -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml -f compose.telegram.dev.yaml up -d telegram_secret_init'
sg docker -c 'docker compose --env-file .env.dev -p oss-control-dev -f compose.dev.yaml -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml -f compose.telegram.dev.yaml run --rm api alembic upgrade head'
sg docker -c 'docker compose --env-file .env.dev -p oss-control-dev -f compose.dev.yaml -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml -f compose.telegram.dev.yaml up -d api web'
sg docker -c 'docker compose --env-file .env.dev -p oss-control-dev -f compose.dev.yaml -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml -f compose.telegram.dev.yaml ps'
```

Не печатайте результирующую Compose-конфигурацию и содержимое volume: в
конфигурации нет значения токена, но диагностические команды должны оставаться
безопасными по умолчанию. Статус файла внутри API проверяется только через
`stat`, без `cat`.

## Отказ и повтор

- `SENT` устанавливается только после ответа Telegram с ожидаемым `chat_id` и
  целочисленным `message_id`.
- Подтверждённый отказ даёт `FAILED`; повтор выполняется новым явным действием.
- Ошибка транспорта после начала запроса даёт `UNCERTAIN`. Автоматический
  повтор запрещён, поскольку сообщение могло быть принято Telegram.
- Каждая попытка и её `client_request_id` сохраняются. Технический аудит не
  содержит токен, текст сообщения или персональные поля.

Проверка реальной отправки выполняется только на синтетической заявке во
временной `oss_test_*` базе. Рабочие dev-заявки нельзя использовать как
тестовые сообщения.

## T2: защищённый allowlist и выключенный poller

`compose.telegram-t2.dev.yaml` содержит отдельный profile `telegram-t2`; без
явного `--profile telegram-t2` он не запускается. API не выполняет polling.
Allowlist хранится у владельца в
`/root/oss-secrets/telegram-allowed-senders.json`, mode `0600`, формат:

```json
{"allowed_user_ids":[123456789]}
```

В реальном файле указываются только проверенные числовые Telegram user ID
монтажников. Имена, usernames и membership группы не заменяют allowlist. Root
init-контейнер копирует файл в отдельный read-only volume с mode `0400`; raw
updates, токен и allowlist не журналируются.

До первого запуска владелец должен:

1. Проверить через Bot API, что webhook не настроен: `getUpdates` с ним
   взаимоисключён.
2. Утвердить текущий `update_id`, с которого разрешён импорт, и записать
   `TELEGRAM_INITIAL_UPDATE_OFFSET` в защищённую dev-конфигурацию. Пустое
   значение намеренно блокирует первый polling, чтобы не импортировать старую
   очередь вслепую.
3. Добавить allowlist в реестр секретов и резервную копию владельца.
4. Запустить profile и проверить только синтетическую передачу во временной БД.

Команда конфигурационной проверки не включает polling:

```bash
sg docker -c 'docker compose --env-file .env.dev -p oss-control-dev -f compose.dev.yaml -f compose.telegram.dev.yaml -f compose.telegram-t2.dev.yaml config --quiet'
```

После отдельного разрешения на живую проверку service запускается добавлением
`--profile telegram-t2`. Одновременно запускается только один consumer этого
бота. Остановка profile не влияет на API и исходящую T1-интеграцию.

## Приём входящего события

- Разрешены только `message` и `callback_query` из LAB chat ID.
- Текст импортируется только как reply на подтверждённый `message_id` handoff.
- Callback содержит `field_done:{handoff_id}`, но дополнительно сверяется с
  чатом и исходным сообщением; одного UUID недостаточно.
- `update_id`, входящий message ID и completion на handoff имеют независимые
  ограничения уникальности. Повтор не создаёт второй комментарий/результат.
- Callback даёт `REPORTED`; кнопка подтверждения в OSS даёт `VERIFIED`.
  `tickets.status`, инженерное решение и закрытие не меняются автоматически.
