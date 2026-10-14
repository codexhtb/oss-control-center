# BDCOM ONU: read-only канал dev

Источник — кастомные таблицы LibreNMS `librenms.bdcom_onus` и
`librenms.bdcom_onu_olts`. У `oss_diagnostics` есть только `SELECT` на эти
таблицы. MariaDB на LibreNMS слушает только `127.0.0.1:3306`; существующий
`oss-librenms-tunnel.service` даёт на `oss-control-dev` защищённый SSH-канал
`127.0.0.1:13306`. Рабочие LibreNMS и UTM5 не изменяются.

## Доступ из Docker

Контейнер API остаётся в сетях dev Compose и не использует host network.
`compose.bdcom.dev.yaml` добавляет маленький proxy-контейнер в том же dev
проекте. Он использует host network **только** для подключения к
`127.0.0.1:13306` и слушает лишь Unix socket
`.dev-secrets/bdcom-socket/bdcom.sock` (`0600`) в каталоге `0700`.
Этот каталог монтируется только в proxy и dev API. TCP-порт MariaDB не
публикуется ни в Docker, ни в MGMT. Proxy не получает пароль к БД.

Пароль уже хранится в `.dev-secrets/bdcom-mariadb-password` (`0600`) и
монтируется только в dev API. В `APP_ENV=dev` адаптер явно отключает TLS
MariaDB на участке внутри SSH-туннеля. Production-конфигурация через
`BDCOM_ONU_CREDENTIAL_FILE` по-прежнему требует CA и проверки TLS;
режим `BDCOM_ONU_TUNNEL_PASSWORD_FILE` при `APP_ENV=production` отвергается.
Секрет не передаётся в URL, аргументах команды, логах или Git.

```sh
install -d -m 700 .dev-secrets/bdcom-socket
sg docker -c 'docker compose -p oss-control-dev --env-file .env.dev -f compose.dev.yaml -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml config --quiet'
sg docker -c 'docker compose -p oss-control-dev --env-file .env.dev -f compose.dev.yaml -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml up -d --no-deps bdcom_tunnel_proxy'
sg docker -c 'docker compose -p oss-control-dev --env-file .env.dev -f compose.dev.yaml -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml up -d --no-deps api'
sg docker -c 'docker compose -p oss-control-dev --env-file .env.dev -f compose.dev.yaml -f compose.monitoring.dev.yaml -f compose.bdcom.dev.yaml ps'
```

После изменения backend-образа сначала выполнить `build api`, затем `up`
с тем же набором Compose-файлов. Web-контейнер обновлять только при
изменении frontend. Проверка источника из API-контейнера должна выводить
только агрегаты, например `COUNT(*)`, без строк ONU и credential.
Health веб-приложения: `https://172.17.7.2:8443/api/v1/health/live` и
`/api/v1/health/ready`. Доступность ONU проверяется отдельным инженерным
запросом; общий readiness не зависит от необязательного источника.

## Данные и ограничения

Проверка реального источника: `bdcom_onus` содержит 2433 строки, из них
2431 с `present=1` (1484 EPON и 947 GPON); `bdcom_onu_olts` содержит 22
OLT. `device_id` и `vlan` имеют тип `int`, `pon_type`, `interface`, `mac`,
`serial`, `state`, `reason` — строковые типы, оптика — `decimal`, а
`last_seen` и `signal_updated_at` — `datetime`. Для `bdcom_onus` есть
индексы `PRIMARY(device_id, mac)`, `idx_device_interface(device_id,
interface)` и `idx_bdcom_onus_vlan(vlan)`; `EXPLAIN` ограниченного запроса
показал использование индекса VLAN. У 27 пар `(device_id, vlan)` более
одной ONU, поэтому неоднозначность является реальной. Все 947 GPON строк
имеют `lower(mac)=lower(serial)`; 1484 действующие EPON строки имеют MAC
в стандартном шестибайтном формате. Проверка не выводила значения полей.

Основной поиск выполняет два параметризованных `SELECT` с `LIMIT 2/21`,
таймаутами три секунды. При нуле по полному ключу допускаются ещё два
ограниченных `SELECT` для **неподтверждённых** кандидатов того же VLAN.
Для доказанной тройки NetUP SQL ограничен
`device_id`, VLAN, `present=1`, типом PON и строгим шаблоном интерфейса
с вычисленным PON-портом. `EXPLAIN` показал индекс `idx_bdcom_onus_vlan`
и оценку одной строки для контрольного EPON-запроса. Пара OLT берётся
только из утверждённого защищённого реестра. Одну ONU по полному ключу
можно показать как `MATCHED_BY_RULE`; ноль, повторы, конфликт NetUP либо
неутверждённый OLT не подтверждают связь. При непроверенном происхождении
NetUP-строки поиск по VLAN остаётся списком кандидатов `UNCONFIRMED`.
15 пар из [таблицы проверки](../research/olt-manual-verification-candidates.md)
утверждены владельцем и добавлены в защищённый dev-реестр как
`manual_verified`; доказательства хранятся в
`.dev-secrets/olt-manual-verification.json` с режимом `0600`.

GPON collector записывает `lower(serial)` в `bdcom_onus.mac` как ключ.
Такой ключ не показывается как физический MAC. Для GPON отображается Serial;
для EPON — доступный MAC. `last_seen` — время последнего опроса ONU,
`signal_updated_at` — время сохранения оптики. Часовой пояс источника пока
не подтверждён (`SOURCE_LOCAL_UNVERIFIED`). Оптика может быть старой;
значения не доказывают текущее состояние линии. Истории событий и оптики
адаптер не читает.

Если SSH-туннель или источник недоступен, запрос возвращает `UNAVAILABLE`,
не подставляя синтетическую ONU. Проверьте `systemctl is-active
oss-librenms-tunnel.service`, состояние proxy и сокета без вывода секрета.
Секрет зарегистрировать в реестре владельца согласно
[`secret-management.md`](secret-management.md); резервную копию подтверждает
владелец инфраструктуры.
