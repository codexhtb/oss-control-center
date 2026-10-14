# Изолированное HTTPS dev-окружение OSS Control Center

Dev-стек запускается как Compose project `oss-control-dev`. Он использует собственные
контейнеры, базу `oss_control_dev` и volume `oss-control-dev_dev_postgres_data`.
Рабочие `oss-control-center-api-1`, `oss-control-center-postgres-1`, база
`oss_control` и их volume в команды ниже не входят. Dev API использует
`BILLING_PROVIDER=NETUP` только для восстановленной лабораторной UTM5.
Рабочий NetUP не подключён.

## Доступ

- Браузер: **https://172.17.7.2:8443/** из MGMT-сети.
- Публикуется только `172.17.7.2:8443`. У PostgreSQL и API нет опубликованных
  портов; PostgreSQL живёт во внутренней Docker network.
- `frontend/Caddyfile.dev` отдаёт React build и проксирует `/api/v1/*` к API
  без подмены browser `Origin`/`Host`. Backend требует точный
  `AUTH_PUBLIC_ORIGIN=https://172.17.7.2:8443`, проверяет CSRF и устанавливает
  `Secure`, `HttpOnly`, `SameSite=Strict` для сессионной cookie.
- Сертификат подписан отдельным локальным CA в `.dev-secrets/certs`. Перед
  импортом сверить SHA-256 fingerprint публичного `.dev-secrets/certs/ca.crt`
  с выводом `openssl x509 -in .dev-secrets/certs/ca.crt -noout -fingerprint -sha256`
  на сервере. Передать Windows только `ca.crt`, затем импортировать в хранилище
  доверенных корневых сертификатов **текущего пользователя**:

  ```powershell
  Import-Certificate -FilePath .\ca.crt -CertStoreLocation Cert:\CurrentUser\Root
  ```

  Закрытый ключ CA (`ca.key`) и серверный ключ (`server.key`) с VM не копировать.
  После лабораторной проверки этот CA можно удалить из Windows. Сертификат
  ограничен IP `172.17.7.2`; через иной hostname браузер его не примет.

## Конфигурация и секреты

`.env.dev.example` описывает параметры без рабочих значений. Реальный
`.env.dev` (`0600`) содержит случайный пароль `oss_dev` и ключ хеширования
сессий. `.dev-secrets/users.json` (`0600`) содержит три синтетических пароля
для `dev-admin`, `dev-operator`, `dev-engineer`. Читать их локально через
защищённый канал владельца; не передавать в Git, чат и shell history.
Временный пароль инженерной dev-учётной записи `bezhan` хранится отдельно в
`.dev-secrets/credentials/bezhan-password` (`0600`), каталог `credentials`
имеет `0700`. Пароль хеширован штатным Argon2id и создан только в
`oss_control_dev`. Backend пока не поддерживает обязательную смену при первом
входе; владелец должен заменить временный пароль до использования вне
контролируемой лаборатории. Значение не включать в команды или документы.
`.dev-secrets/registry.tsv` (`0600`) содержит только метаданные и пути.
Лабораторный runtime credential находится в игнорируемом Git файле
`.dev-secrets/netup-runtime.env` (`0600`), подготовленном из существующего
`.env.netup-lab` без смены пароля. Пример без секрета —
[`dev-runtime.env.example`](../netup/dev-runtime.env.example). Docker build
исключает этот файл. Compose передаёт его только dev API в режиме
`APP_ENV=dev`, `NETUP_PURPOSE=lab`. Лабораторная MariaDB не поддерживает TLS;
plaintext разрешён лишь для private IP `172.17.7.1`. Production-валидация
этот режим отклоняет. API имеет исходящий доступ через dev `edge` network,
но не публикует порт. PostgreSQL остаётся во внутренней сети.
Выбранный для browser smoke лабораторный ЛС хранится отдельно в
`.dev-secrets/netup-smoke.env` (`0600`) и передаётся только одноразовому
Chromium-контейнеру; его значение не находится в Git или отчётах.
Каталоги `.dev-secrets` и `certs` имеют `0700`. `root` может прочесть файлы;
копия в `/root/oss-secrets/` и зашифрованный backup остаются задачей владельца.
Состояние реестра: **backup pending** до проверенного восстановления.

Опциональная интеграция LibreNMS использует только read-only токен владельца
в `.dev-secrets/librenms-token` (`0600`) и проверенный реестр соответствий в
`.dev-secrets/monitoring-mappings.json` (`0600`). Токен передаётся API как
read-only mount файла, не как переменная со значением. Для этого override
`compose.monitoring.dev.yaml` запускает API от непривилегированного UID 1001,
совпадающего с владельцем файла. `LIBRENMS_URL` задаёт origin
`http://172.17.7.70`; адаптер добавляет `/api/v0` самостоятельно. HTTP
разрешён только в лабораторном `APP_ENV=dev` с явным opt-in; для постоянной
эксплуатации нужен TLS или защищённый канал. PostgreSQL не публикуется.
Подтверждённые соответствия оборудования и портов вносятся в защищённый
реестр отдельно; пустой реестр означает `UNKNOWN`, даже если LibreNMS API
доступен. Владелец утвердил только уникальные точные совпадения имён; генератор
`dev/build_monitoring_mappings.py` использует read-only `SELECT` к NetUP Lab и
`GET /api/v0/devices` к LibreNMS, не выводит имена, IP и IDs и никогда не
сопоставляет порты. Для повторной генерации нужен пустой защищённый файл и
явный флаг `--approve-unique-exact-sysname`; текущий реестр перезаписывать
нельзя без отдельной сверки. При смене файла Compose API надо пересоздать,
поскольку bind mount держит исходный inode. Icinga2 не подключена.

Повторное построение реестра после проверки источников (без вывода реквизитов):

```bash
test ! -e .dev-secrets/monitoring-mappings.next.json
umask 077
printf '{"devices":[]}\n' > .dev-secrets/monitoring-mappings.next.json
set -a
. ./.env.netup-lab
set +a
APP_ENV=dev NETUP_LAB_ALLOW_PLAINTEXT=true \
  LIBRENMS_URL=http://172.17.7.70 \
  LIBRENMS_TOKEN_FILE=.dev-secrets/librenms-token \
  MONITORING_ALLOW_HTTP_DEV=true \
  .venv/bin/python -m dev.build_monitoring_mappings \
  --approve-unique-exact-sysname \
  --output .dev-secrets/monitoring-mappings.next.json
```

Генератор выводит только количество пар. Сверьте его с ожидаемым диапазоном,
затем вручную замените защищённый реестр и пересоздайте только dev API. Реестр
содержит сетевые идентификаторы и не попадает в Git. Тестовый ЛС с активным
сопоставлением хранится в `.dev-secrets/monitoring-smoke.env` (`0600`); это
реальное лабораторное значение, его нельзя выводить в журнал или коммит.
Read-only Chromium-проверка через HTTPS:

```bash
sg docker -c 'OSS_READ_ONLY=1 OSS_MONITORING_ONLY=1 OSS_SMOKE_ENV_FILE=/opt/oss-control-center/.dev-secrets/monitoring-smoke.env sh dev/run_browser_e2e.sh'
```

Она проверяет факт получения состояния устройства из LibreNMS, явный
`UNKNOWN` для порта, RBAC, CSRF и Origin; новых заявок не создаёт.

Администратор может создать отдельную защищённую копию без вывода значений.
Перед командами проверить, что целевые файлы ещё не существуют:

```bash
sudo install -d -o root -g root -m 0700 /root/oss-secrets/oss-control-dev
sudo install -o root -g root -m 0600 /opt/oss-control-center/.env.dev /root/oss-secrets/oss-control-dev/.env.dev
sudo install -o root -g root -m 0600 /opt/oss-control-center/.dev-secrets/users.json /root/oss-secrets/oss-control-dev/users.json
sudo install -o root -g root -m 0600 /opt/oss-control-center/.dev-secrets/credentials/bezhan-password /root/oss-secrets/oss-control-dev/bezhan-password
sudo install -o root -g root -m 0600 /opt/oss-control-center/.dev-secrets/netup-runtime.env /root/oss-secrets/oss-control-dev/netup-runtime.env
sudo install -o root -g root -m 0600 /opt/oss-control-center/.dev-secrets/netup-smoke.env /root/oss-secrets/oss-control-dev/netup-smoke.env
sudo install -o root -g root -m 0600 /opt/oss-control-center/.dev-secrets/certs/ca.key /root/oss-secrets/oss-control-dev/ca.key
sudo install -o root -g root -m 0600 /opt/oss-control-center/.dev-secrets/certs/server.key /root/oss-secrets/oss-control-dev/server.key
sudo install -o root -g root -m 0600 /opt/oss-control-center/.dev-secrets/registry.tsv /root/oss-secrets/oss-control-dev/registry.tsv
```

После этого внести пути копий в общий `/root/oss-secrets/registry.tsv`, включить
каталог в зашифрованный off-host backup и проверить восстановление в изоляции.
Локальная копия на той же VM ещё не является проверенным резервным копированием.

Новые секреты создавать только если защищённых файлов ещё нет; не запускать
повторную генерацию поверх существующих. Runtime API не имеет доступа к
`users.json` и закрытому ключу CA. Разовый `seed` получает файл пользователей
только через read-only mount.

## Запуск и проверка

Все команды выполнять в `/opt/oss-control-center` под `codexdev`.
Текущей SSH-сессии может требоваться `sg docker`; `sudo` не нужен.

```bash
sg docker -c 'docker compose --env-file .env.dev -f compose.dev.yaml config --quiet'
sg docker -c 'docker compose --env-file .env.dev -f compose.dev.yaml build api web'
sg docker -c 'docker compose --env-file .env.dev -f compose.dev.yaml up -d postgres'
sg docker -c 'docker compose --env-file .env.dev -f compose.dev.yaml run --rm api alembic upgrade head'
sg docker -c 'docker compose --env-file .env.dev -f compose.dev.yaml --profile tools run --rm seed'
sg docker -c 'docker compose --env-file .env.dev -f compose.dev.yaml up -d api web'
sg docker -c 'docker compose --env-file .env.dev -f compose.dev.yaml ps'
python3 dev/smoke_https.py
```

Для dev-стека с LibreNMS добавьте `-f compose.monitoring.dev.yaml` к каждой
команде Compose после `-f compose.dev.yaml`. Перед запуском проверьте
`docker compose ... config --quiet` и права обоих защищённых файлов. Не
подставляйте значения токена в shell arguments или логи. Выключение override
оставляет код доступным, но источник LibreNMS не настроен.

`seed` запускается только на пустой dev-базе и при повторном вызове откажет,
не меняя пользователей. Проверки с подтверждением локального CA:

```bash
curl --cacert .dev-secrets/certs/ca.crt -sS -o /dev/null -w '%{http_code}\n' https://172.17.7.2:8443/
curl --cacert .dev-secrets/certs/ca.crt -sS https://172.17.7.2:8443/api/v1/health/live
curl --cacert .dev-secrets/certs/ca.crt -sS https://172.17.7.2:8443/api/v1/health/ready
sg docker -c 'docker compose --env-file .env.dev -f compose.dev.yaml run --rm api alembic current'
```

`dev/smoke_https.py` проверяет анонимный вход, все три роли, права на заявки,
cookie, CSRF, Origin, logout и rate limit. Секреты и cookies не печатаются.
Для реального браузерного smoke в одноразовом Chromium-контейнере (образ
Playwright `v1.64.0-noble`, npm пакет той же версии) выполните
`sg docker -c 'sh dev/run_browser_e2e.sh'`. Тестовый контейнер временно добавляет
dev CA в собственный NSS trust store и не отключает TLS-проверку. Он проверяет
форму входа, создание всех трёх типов заявок, явный выбор неоднозначных связей,
комментарий оператора, назначение, начало работы, диагностику, решение,
закрытие, `If-Match`, RBAC, CSRF/Origin и повторный вход dev-инженера.
При `BILLING_PROVIDER=NETUP` он создаёт только синтетические `MANUAL` и
`INTERNAL` заявки и проверяет поиск ЛС в NetUP Lab через браузер без создания
linked заявки с реальными данными. Ранее в mock-режиме проверены все три типа.
Для проверки Subscriber 360 и уже существующей связанной заявки без создания
новых заявок используйте `sg docker -c 'OSS_READ_ONLY=1 sh dev/run_browser_e2e.sh'`.
Это также проверяет переход из заявки обратно в карточку абонента.
Тестовый контейнер не публикует порты и не меняет хранилище сертификатов VM.
Для защищённых скриншотов синтетических экранов:

```bash
sg docker -c 'OSS_SCREENSHOTS=1 sh dev/run_browser_e2e.sh'
```

Скриншоты сохраняются в игнорируемом Git каталоге `.dev-secrets/screenshots`.
Проверка HTML-атрибутов формы выполнена в Chromium; всплывающее предложение
сохранить пароль в конкретном браузере Windows требует ручной проверки владельца.

Полный backend pytest на временных PostgreSQL базах и frontend gates:

```bash
sg docker -c 'docker compose --env-file .env.dev -p oss-control-dev -f compose.dev.yaml run --rm -e BILLING_PROVIDER=MOCK -e RUN_DB_TESTS=1 api python -m pytest -q'
cd frontend && npm run typecheck && npm test -- --run && npm run build
```

## Остановка, повторный запуск и данные

```bash
sg docker -c 'docker compose --env-file .env.dev -f compose.dev.yaml stop'
sg docker -c 'docker compose --env-file .env.dev -f compose.dev.yaml up -d'
```

`stop` и `up` сохраняют dev volume. Не использовать `down -v` без отдельного
решения об удалении всех dev-данных. Изменение `.env.dev` с паролем PostgreSQL
после первого запуска не меняет пароль внутри существующего volume: нужен
согласованный процесс ротации. Перед удалением dev volume сделать защищённый
backup базы и проверить восстановление. Дамп содержит даже синтетические
учётные данные и хеши, поэтому хранить его с режимом `0600` вне Git.

## Границы текущего UI

Stage 3C содержит карточку Subscriber 360 с provenance, несколькими услугами,
IP-группами, финансовыми сведениями для оператора и историей обращений.
Новые lookup читаются из восстановленной NetUP Lab, а старые mock snapshots и
синтетические заявки остаются в dev PostgreSQL. Данные Lab в lookup snapshot
защищены RBAC; не сохраняйте скриншоты таких карточек или их содержимое в
общих журналах. Автоматического fallback на MOCK нет. Контролируемая очистка
lookup snapshots без заявок возможна после 24 часов; расписание очистки пока
не настроено. Production NetUP не задействован.
