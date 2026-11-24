# Notification Center: release и rollback

Рабочий HTTPS-портал обслуживается исторически названным Compose project
`oss-control-dev`. Это не основание считать данные тестовыми. Перед релизом
проверить labels `project.config_files` работающих API/WEB: используются
`compose.dev.yaml`, `compose.telegram.dev.yaml`, `compose.telegram-t2.dev.yaml`,
`compose.monitoring.dev.yaml`, `compose.bdcom.dev.yaml`. Legacy project
`oss-control-center` не обновляется этим релизом.

## Gates

1. Ruff изменённых Python-файлов, `git diff --check`, полный backend pytest с
   `BILLING_PROVIDER=MOCK`, `RUN_DB_TESTS=1`, disposable PostgreSQL databases.
2. Frontend typecheck, полный Vitest и production build. Отдельный regression
   в API-клиенте: пустой 204 с JSON content-type не должен декодироваться как JSON.
3. Построить неизменяемые release images; проверить их в отдельном DEV-стеке:
   [run_notification_center_e2e.sh](../../dev/run_notification_center_e2e.sh).
   Ни одна рабочая БД не переключается на тестовую.
4. Зафиксировать изменения Git и отправить проверенный commit. Сохранить старые
   образы API/WEB и защищённый PostgreSQL backup, проверить restore в отдельную
   `oss_test_*` базу. Новая миграция не требуется, schema head остаётся `0053`.
5. Только после всех gates обновить API и WEB с `up -d --no-deps --wait api web`
   и полным списком исходных overlays. Не запускать общий `up`, `seed`, token init,
   Telegram poller, PostgreSQL или proxy; не менять NetUP/репликацию.
6. Проверить health, TLS портал/API, Field Work на 8443 и ограничения его путей.
   [Read-only Chromium smoke](../../dev/browser_notification_center_readonly.cjs)
   проверяет engineer/admin/operator, tabs и заявки без прочтения рабочих событий.
   Сверить container IDs PostgreSQL, poller, proxy и legacy project до/после.

Если сетевой доступ к Python base image мешает штатной сборке, допустим source
layer поверх сохранённого текущего API image: зависимости не менялись. Dockerfile
должен содержать только `FROM <saved-runtime>` и `COPY app /app/app`,
`COPY tests /app/tests`; применить один и тот же полученный image в DEV и PROD.
WEB собирается штатным `frontend/Dockerfile.runtime`. Не выполнять новую сборку
между успешной приёмкой и promotion проверенных images.

## Rollback

Для этого релиза до promotion сохранены:
`oss-control-dev-api:pre-notification-center-20261124` и
`oss-control-dev-web:pre-notification-center-20261124`.
При провале live health/smoke вернуть оба образа:

```bash
docker image tag oss-control-dev-api:pre-notification-center-20261124 oss-control-dev-api:local
docker image tag oss-control-dev-web:pre-notification-center-20261124 oss-control-dev-web:local
docker compose --env-file .env.dev -p oss-control-dev \
  -f compose.dev.yaml -f compose.telegram.dev.yaml -f compose.telegram-t2.dev.yaml \
  -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml \
  up -d --no-deps --wait api web
```

Повторить health и HTTPS boundary probes. Не восстанавливать PostgreSQL поверх
рабочих данных: схема не менялась и read receipts совместимы с прежним кодом.
Старые образы сохраняются после релиза; DB backup остаётся отдельной защищённой
локальной копией. Проверенный локальный restore не подтверждает off-host backup.

Секреты этого этапа не создаются и не ротируются; DEV seed использует уже
существующий manifest только в disposable database. Runtime не получает manifest.

## Live smoke identities

Read-only Chromium принимает manifest `{login: password}` из защищённого
`/run/dev-users.json`; имена не зашиты в runner. Не предполагается, что старые
DEV-пользователи существуют в рабочих данных. Для релиза 24.11.2026 прежние
DEV-записи отсутствовали, а сохранённый старый credential не совпадал. Первый
live smoke выполнил автоматический rollback; затем подготовлены три отдельные
временные smoke identity с минимальными разрешениями для engineer/admin/operator.
Существующие аккаунты, пароли и персональные grants не меняются.

Создание и удаление выполняются штатным identity service с аудитом; после
проверки все временные записи удаляются мягко, а сессии отзываются. Никаких
заявок или прочтений рабочих уведомлений live smoke не создаёт. DEV runner
проверяет этот же read-only script перед production promotion.

Manifest хранится в `.dev-secrets/credentials/notification-center-smoke.json`
(`0600`, родитель `0700`), проверенная локальная копия —
`.dev-secrets/backups/notification-center-smoke.json` (`0600`). В локальном
`.dev-secrets/registry.tsv` регистрируются только метаданные. Владелец может
прочесть файлы как root; codexdev не обходит запрет доступа к `/root/oss-secrets`.
После проверки отсутствия целевого файла владелец может сохранить свою копию:

```bash
sudo install -d -o root -g root -m 0700 /root/oss-secrets/notification-center-20261124
sudo install -o root -g root -m 0600 \
  /opt/oss-control-center/.dev-secrets/credentials/notification-center-smoke.json \
  /root/oss-secrets/notification-center-20261124/smoke-users.json
```

Добавить путь в реестр владельца. Зашифрованная off-host копия этих временных
credentials не подтверждена; локальная копия сверена побайтно. После удаления
smoke identities сохранённые пароли не дают доступа к порталу.
