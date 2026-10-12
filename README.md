# OSS Control Center — Backend Foundation (Phase 1.1)

Минимальный FastAPI backend с PostgreSQL, SQLAlchemy 2 и Alembic. Сейчас доступны только `GET /api/v1/health/live` и `GET /api/v1/health/ready`. Реальные NetUP, Telegram, LibreNMS, Icinga и BDCOM не подключаются; бизнес-логика Service Desk остаётся для Phase 1.2.

Приложение использует Python 3.12 внутри контейнера. На Ubuntu 26.04 не требуется устанавливать Python или PostgreSQL для приложения в ОС. Перед запуском нужен доступ текущего пользователя к Docker socket; не обходите отсутствие доступа через изменение групп или прав сокета.

## Настройка dev stack

Выполняйте команды из корня репозитория `/opt/oss-control-center` под пользователем, которому уже разрешён Docker:

```bash
cp .env.example .env
chmod 600 .env
```

Задайте в `.env` уникальный локальный `POSTGRES_PASSWORD` перед любым запуском. Пустой пароль не допускается. Значения из `.env.example` предназначены только для dev и не подходят для production. `.env` исключён из Git и Docker build context.

| Переменная | Назначение |
| --- | --- |
| `APP_ENV` | `dev`, `test` или `production`; dev stack использует `dev` |
| `API_PORT` | Порт API на `127.0.0.1` VM; по умолчанию `8000` |
| `POSTGRES_DB` | Имя базы приложения |
| `POSTGRES_USER` | Пользователь PostgreSQL dev stack |
| `POSTGRES_PASSWORD` | Уникальный локальный пароль, обязательный |
| `READINESS_TIMEOUT_SECONDS` | Ограничение для pool checkout, соединения и SQL-запроса; 1–5 секунд |

Сначала проверяйте итоговую конфигурацию и право доступа к Docker, затем запускайте:

```bash
docker compose config --quiet
docker info --format '{{.ServerVersion}}'
docker compose up --build -d
docker compose ps
```

PostgreSQL не публикует порт на VM и доступен только в сети Compose. API публикуется только на `127.0.0.1`; его процесс внутри контейнера работает как UID/GID `10001`, без root и privileged mode. Данные PostgreSQL находятся в именованном volume `postgres_data`.

## Миграции

```bash
docker compose run --rm api alembic upgrade head
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version"'
```

Ревизия `0001_foundation` создаёт только служебную таблицу Alembic. Доменные таблицы не нужны для Phase 1.1 и будут добавлены контролируемой миграцией при реализации Service Desk.

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

Тесты PostgreSQL создают собственную временную базу с префиксом `oss_test_` на dev Postgres, проверяют соединение и применение Alembic к пустой БД и удаляют только эту временную базу. Без `RUN_DB_TESTS=1` интеграционные тесты пропускаются; endpoint и configuration тесты продолжают работать. Тесты не должны запускаться против production: fixture требует `APP_ENV=dev` и `POSTGRES_HOST=postgres`.

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
