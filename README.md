# OSS Control Center — Backend Foundation и Identity (Phase 1.2A)

FastAPI backend с PostgreSQL, SQLAlchemy 2, Alembic, health endpoints и локальной аутентификацией/RBAC. Бизнес-логика заявок и Subscriber 360 пока не реализована. Реальные NetUP, Telegram, LibreNMS, Icinga и BDCOM не подключаются.

Приложение использует Python 3.12 внутри контейнера. На Ubuntu 26.04 не требуется устанавливать Python или PostgreSQL для приложения в ОС. Перед запуском нужен доступ текущего пользователя к Docker socket; не обходите отсутствие доступа через изменение групп или прав сокета.

## Настройка dev stack

Выполняйте команды из корня репозитория `/opt/oss-control-center` под пользователем, которому уже разрешён Docker:

```bash
cp .env.example .env
chmod 600 .env
```

Задайте в `.env` уникальные локальные `POSTGRES_PASSWORD` и `AUTH_HASH_KEY` перед любым запуском. Для ключа используйте не менее 32 случайных байт, например выведите значение командой `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'` и внесите его в `.env`. Пустые значения не допускаются. Значения из `.env.example` предназначены только для dev и не подходят для production. `.env` исключён из Git и Docker build context. Сохраните `AUTH_HASH_KEY` в защищённом хранилище: его смена изменит HMAC-ключи лимитов и аудита.

| Переменная | Назначение |
| --- | --- |
| `APP_ENV` | `dev`, `test` или `production`; dev stack использует `dev` |
| `API_PORT` | Порт API на `127.0.0.1` VM; по умолчанию `8000` |
| `POSTGRES_DB` | Имя базы приложения |
| `POSTGRES_USER` | Пользователь PostgreSQL dev stack |
| `POSTGRES_PASSWORD` | Уникальный локальный пароль, обязательный |
| `READINESS_TIMEOUT_SECONDS` | Ограничение для pool checkout, соединения и SQL-запроса; 1–5 секунд |
| `AUTH_PUBLIC_ORIGIN` | Точный внешний origin для проверки браузерных `Origin` и `Host`; dev пример `http://127.0.0.1:8000` |
| `AUTH_ALLOW_HTTP_LOOPBACK` | Только для `APP_ENV=dev` и `AUTH_PUBLIC_ORIGIN=http://127.0.0.1:<port>`; в остальных окружениях `false` |
| `AUTH_HASH_KEY` | Случайный постоянный секрет для HMAC логина/источника; не менее 32 байт |

Сначала проверяйте итоговую конфигурацию и право доступа к Docker, затем запускайте:

```bash
docker compose config --quiet
docker info --format '{{.ServerVersion}}'
docker compose build api
docker compose up -d postgres
docker compose run --rm api alembic upgrade head
docker compose up -d api
docker compose ps
```

PostgreSQL не публикует порт на VM и доступен только в сети Compose. API публикуется только на `127.0.0.1`; его процесс внутри контейнера работает как UID/GID `10001`, без root и privileged mode. Данные PostgreSQL находятся в именованном volume `postgres_data`.

## Миграции

```bash
docker compose run --rm api alembic upgrade head
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version"'
```

Ревизия `0001_foundation` создаёт только служебную таблицу Alembic. Ревизия `0002_identity_rbac` добавляет пользователей, серверные сессии, лимиты входа и аудит identity. Таблицы Service Desk будут добавлены отдельной миграцией.

## Первоначальный администратор и Identity API

После миграции создайте первого администратора через интерактивный терминал. Команда работает только при пустой таблице пользователей; логин, отображаемое имя и пароль вводятся вручную, пароль не виден в терминале:

```bash
docker compose run --rm -it api python -m app.modules.identity.bootstrap_admin
```

Администратор создаёт оператора и инженеров через `POST /api/v1/users`. Самостоятельной регистрации нет. Доступны `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`, `GET /api/v1/auth/permissions`, `POST /api/v1/users/{user_id}/deactivate` и `PATCH /api/v1/users/{user_id}/role`. Подробности запросов и матрица прав описаны в [API contracts](docs/architecture/api-contracts.md) и [RBAC](docs/architecture/rbac.md). При HTTPS session cookie `HttpOnly`, `Secure`, `SameSite=Strict`; dev HTTP допускается только на явно настроенном loopback. Изменяющие запросы требуют `Origin` и `X-CSRF-Token`, равный значению `oss_csrf` cookie. Сервис работает с прямым адресом TCP-клиента и не доверяет proxy-заголовкам.

## Health checks

```bash
curl -i http://127.0.0.1:8000/api/v1/health/live
curl -i http://127.0.0.1:8000/api/v1/health/ready
```

Liveness возвращает `200` при работающем процессе. Readiness выполняет `SELECT 1` в PostgreSQL и возвращает `503`, когда соединение недоступно. Для ручной проверки отрицательного сценария в dev stack можно остановить только БД этого проекта, проверить `503`, затем запустить её снова:

```bash
docker compose stop postgres
curl -i http://127.0.0.1:8000/api/v1/health/ready
docker compose start postgres
```

## Тесты и Ruff в Docker

```bash
docker compose run --rm api ruff check app tests
docker compose run --rm -e RUN_DB_TESTS=1 api pytest -q
```

Тесты PostgreSQL создают собственные временные базы с префиксом `oss_test_` на dev Postgres, проверяют миграции, identity API и инварианты, затем удаляют только эти базы. Основная БД не заполняется тестовыми пользователями. Без `RUN_DB_TESTS=1` интеграционные тесты пропускаются; health и configuration тесты продолжают работать. Тесты нельзя запускать против production: fixture требует `APP_ENV=dev` и `POSTGRES_HOST=postgres`.

## Сохранность данных и резервное копирование

`docker compose restart postgres` сохраняет именованный volume. После миграции проверьте сохранность ревизии запросом из раздела «Миграции» до и после перезапуска. Для базовой резервной копии:

```bash
umask 077
docker compose exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > oss-control.dump
```

Файл дампа может содержать персональные данные. Храните его в защищённом месте; `*.dump` исключён из Git. Проверка восстановления в отдельной базе нужна перед использованием процедуры в production.

## Остановка

```bash
docker compose down
```

Команда сохраняет `postgres_data`. Не используйте `down --volumes`, если данные нужно сохранить.

## Если Docker socket недоступен

Не запускайте команды через `sudo` и не меняйте права сокета или группы ради проверки. Передайте перечисленные выше команды администратору либо пользователю с уже утверждённым доступом к Docker. Файлы проекта и `docker compose config --quiet` можно проверить без доступа к daemon.
