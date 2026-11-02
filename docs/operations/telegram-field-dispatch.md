# Telegram Field Dispatch T1/T2 — dev LAB

T1 отправляет исходящие сообщения в текущую группу, привязанную командой
`/oss_bind`. Сохранение причины выезда создаёт `DRAFT`; сетевой запрос
выполняется только после отдельного нажатия «Отправить боту». T2 принимает
проверенные ответы и callback «✅ Выполнено». Нажатие вызывает ForceReply
«Что было сделано?»; ответить может любой обычный участник привязанной группы.
Автоматическое закрытие отсутствует. Адресат каждого уже отправленного handoff
сохраняется независимо от последующих привязок.

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

## T2: динамическая группа и poller

`compose.telegram-t2.dev.yaml` содержит отдельный profile `telegram-t2`. API не
выполняет polling. Первая привязка выполняется в целевой группе командой
`/oss_bind`: вызывающий пользователь должен быть её администратором, а бот
должен иметь права администратора. `/oss_status` показывает сохранённую группу.
Участие человека в группе само по себе не является правом доступа к OSS:
сообщение импортируется только как reply на известную передачу или ForceReply.

Текущая привязка, её аудит и group→supergroup aliases сохраняются в PostgreSQL.
Обновления `my_chat_member` блокируют отправку при удалении бота и восстанавливают
её после возврата. Старые отправки продолжают принимать ответы в собственном
чате, а новые используют текущую группу. Статический sender allowlist больше не
используется; старый файл владельца не требуется для работы и не удаляется.

Poller не запускается, если отсутствовал до обновления. Перед первым включением
владелец явно задаёт `TELEGRAM_INITIAL_UPDATE_OFFSET`; пустое значение блокирует
первый polling, чтобы не импортировать старую очередь вслепую. Проверка
конфигурации не включает polling:

```bash
sg docker -c 'docker compose --env-file .env.dev -p oss-control-dev -f compose.dev.yaml -f compose.telegram.dev.yaml -f compose.telegram-t2.dev.yaml config --quiet'
```

Одновременно работает только один consumer этого бота. Реальные тестовые
сообщения в рабочие группы не отправляются.

## Приём входящего события

- Обрабатываются `message`, `callback_query` и `my_chat_member` только из текущей привязки или чата известного handoff.
- Текст импортируется только как reply на подтверждённый `message_id` ForceReply; произвольный текст и reply на исходную передачу игнорируются.
- Callback содержит `field_done:{handoff_id}`, но дополнительно сверяется с
  чатом и исходным сообщением; одного UUID недостаточно.
- `update_id`, входящий message ID и completion на handoff имеют независимые
  ограничения уникальности. Повтор не создаёт второй комментарий/результат.
- Callback создаёт prompt, но работы остаются `AWAITING_REPORT`. Только
  ответ на подтверждённый `message_id` вопроса от любого обычного участника той же группы
  даёт `REPORTED`; кнопка подтверждения в OSS даёт `VERIFIED`.
  `tickets.status`, инженерное решение и закрытие не меняются автоматически.
- Перед отправкой вопроса состояние становится `UNCERTAIN`. Если процесс
  прервался, автоматический повтор запрещён: сначала сверить факт отправки
  и `message_id` в Telegram. Подтверждённый отказ даёт `FAILED` и допускает
  новое нажатие кнопки. Старые T2 callbacks до `0022` остаются в истории.

## Обновление dev до `0023`

Остановите только `telegram_poller`, оставив API, исходящую отправку и другие
интеграции работающими. Перед миграцией сохраните `pg_dump -Fc` постоянной
dev-БД в `.dev-secrets/backups/` (`0700`, файл `0600`), восстановите его в
новую `oss_test_*` базу и сверьте `alembic_version`, заявки, пользователей,
историю, аудит и записи field dispatch. Проверьте upgrade `0022 → 0023` и
приёмку на одноразовой базе. Затем примените `alembic upgrade head` к dev,
перезапустите только API, web и opt-in poller через полный набор Compose
override. Не используйте `down -v`, seed или live тестовые сообщения для
рабочих dev-заявок. Защищённый backup остаётся локальным до подтверждения
зашифрованной копии владельца вне VM.
