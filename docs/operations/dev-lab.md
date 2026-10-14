# Изолированное HTTPS dev-окружение OSS Control Center

Dev-стек запускается как Compose project `oss-control-dev`. Он использует собственные
контейнеры, базу `oss_control_dev` и volume `oss-control-dev_dev_postgres_data`.
Рабочие `oss-control-center-api-1`, `oss-control-center-postgres-1`, база
`oss_control` и их volume в команды ниже не входят. В первой версии выбран
`BILLING_PROVIDER=MOCK`; лабораторная UTM5 не подключена.

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
`.dev-secrets/registry.tsv` (`0600`) содержит только метаданные и пути.
Каталоги `.dev-secrets` и `certs` имеют `0700`. `root` может прочесть файлы;
копия в `/root/oss-secrets/` и зашифрованный backup остаются задачей владельца.
Состояние реестра: **backup pending** до проверенного восстановления.

Администратор может создать отдельную защищённую копию без вывода значений.
Перед командами проверить, что целевые файлы ещё не существуют:

```bash
sudo install -d -o root -g root -m 0700 /root/oss-secrets/oss-control-dev
sudo install -o root -g root -m 0600 /opt/oss-control-center/.env.dev /root/oss-secrets/oss-control-dev/.env.dev
sudo install -o root -g root -m 0600 /opt/oss-control-center/.dev-secrets/users.json /root/oss-secrets/oss-control-dev/users.json
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
форму входа, рабочий экран и выход каждой роли, не публикует порты и не меняет
хранилище сертификатов VM.

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

Stage 3A содержит вход, выход, обзор и страницу прав. Экраны заявок и
Subscriber 360 запланированы в Stage 3B/3C и не являются частью этого запуска.
Данные биллинга в этом dev-стеке синтетические. NetUP Lab можно подключать
отдельным проверяемым этапом после согласования runtime-конфигурации и доступа
только на чтение; текущий стек не нуждается в plaintext MariaDB-доступе.
