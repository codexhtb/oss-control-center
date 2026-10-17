# Telegram Field Dispatch T1 — dev LAB

T1 отправляет только исходящие сообщения в фиксированную LAB-группу
`-5319791765`. Сохранение причины выезда создаёт `DRAFT`; сетевой запрос
выполняется только после отдельного нажатия «Отправить боту». Входящие ответы,
автоматическое закрытие и отправка в произвольные чаты отсутствуют.

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
