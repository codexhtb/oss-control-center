# BDCOM ONU: лабораторный read-only канал

Владелец выбрал отдельный MariaDB account LibreNMS, ограниченный источником
`172.17.7.2` и правом `SELECT` только на `librenms.bdcom_onus` и
`librenms.bdcom_onu_olts`. Его готовность **ещё не подтверждена**. До
подтверждения не подключаться к MariaDB LibreNMS и не добавлять
`compose.bdcom.dev.yaml` к запущенному стеку. Базовый API возвращает
`UNAVAILABLE`, Web UI явно показывает отсутствие подключения. Production
LibreNMS и его schema/data не изменяются.

После передачи владельцем пути к защищённому файлу допускается создать
доступный `codexdev` файл `.dev-secrets/bdcom-onu-db.json` (`0600`) и
зарегистрировать путь в локальном реестре секретов. Каталог `.dev-secrets`
остаётся `0700`; содержимое файла не выводить. JSON содержит ровно ключи:
`host` (строка), `port` (число), `database` (строка `librenms`), `user`
(строка), `password` (строка), `tls_ca` (путь CA или `null`),
`allow_plaintext_dev` (boolean). Без TLS разрешено только при `APP_ENV=dev`
и явном `allow_plaintext_dev=true`; production требует CA и проверку TLS.
Путь задаётся только через `BDCOM_ONU_CREDENTIAL_FILE`. Сервис не принимает
пароль в URL, query, request payload или CLI argument.

Подключать Compose overlay к **изолированному** проекту:

```sh
docker compose -p oss-control-dev -f compose.dev.yaml -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml config --quiet
```

Пока источник выключен, браузерная read-only проверка подтверждает RBAC,
контракт `UNAVAILABLE` и отсутствие выдуманной ONU без создания заявок:

```sh
sg docker -c 'OSS_READ_ONLY=1 OSS_MONITORING_ONLY=1 OSS_BDCOM_EXPECT_UNAVAILABLE=1 OSS_SMOKE_ENV_FILE=/opt/oss-control-center/.dev-secrets/monitoring-smoke.env sh dev/run_browser_e2e.sh'
```

При тестировании не применять миграции к `oss_control` и не менять LibreNMS.
SQL-адаптер выполняет ровно два параметризованных `SELECT` по `device_id`,
`vlan` и `present=1`, с `LIMIT 2/21`, таймаутами 3 секунды, без записи. Время
последнего опроса конкретной ONU берётся из `bdcom_onus.last_seen`. Перед
включением необходимо проверить реальную схему полей, индекс `(device_id,
vlan)` и сетевой путь из dev API; если индекса нет, измерить EXPLAIN и
объём таблицы до боевого запроса. Историю событий/сигнала адаптер не читает.

Endpoint возвращает **кандидатов**, отобранных по подтверждённому OLT и VLAN
NetUP, но `association=UNCONFIRMED` даже при одном результате. Инженер должен
подтвердить связь независимым идентификатором; автоматического подтверждения
и сохранения в ticket history пока нет. GPON `bdcom_onus.mac` содержит
суррогат `lower(serial)` и не отображается как физический MAC. Значения
оптики относятся к ежедневному сбору. `signal_updated_at` общий для трёх
полей и не доказывает время каждого значения. Timestamps без подтверждённой
зоны помечаются `SOURCE_LOCAL_UNVERIFIED`.

Новый credential добавляется в защищённый реестр владельца по
[`secret-management.md`](secret-management.md); резервную копию делает
владелец инфраструктуры. В Git попадает только Compose overlay и этот
runbook, не файл credentials.
