# Deployment

## Источники конфигурации

Tracked Compose описывает базовые/изолированные конфигурации (`compose.yaml`, `compose.dev.yaml` и точечные overlays). `compose.dev.yaml` именует проект `oss-control-dev`; наличие другого Compose-проекта на узле не делает его целью выкладки. На read-only snapshot 18.11.2026 API/WEB использовали overlays `compose.dev.yaml`, `compose.telegram.dev.yaml`, `compose.telegram-t2.dev.yaml`, `compose.monitoring.dev.yaml`, `compose.bdcom.dev.yaml`. Точный production Compose set на момент будущего deployment, image tag/digest, DNS/DNAT и owner approval **UNVERIFIED** в Git; перепроверяйте labels.

Read-only проверка на момент аудита 18.11.2026: контейнеры проекта `oss-control-dev` были видимы; PostgreSQL, API, WEB и BDCOM proxy сообщали healthy, Telegram poller был запущен; `alembic current` API сообщил `0052_field_work_note`. Это snapshot, не гарантия текущего состояния и не подтверждение внешнего домена.

## Безопасный порядок

1. Получить письменное разрешение на конкретную среду, commit и список сервисов.
2. Сверить `git status`, `git rev-parse HEAD`, `git show --stat`, полный staged diff и Compose labels `project`, `working_dir`, `config_files` у уже существующих контейнеров. Для labels достаточно `docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' <container>`; не выводите `.Config.Env`.
3. Снять health, текущую revision и контрольные counts через read-only запросы; подтвердить PostgreSQL volume и backup/restore gate.
4. Проверить тесты и собрать ожидаемые образы отдельно от работающих контейнеров.
5. Для schema change использовать [migration procedure](migration-procedure.md), только после разрешения и проверки восстановления.
6. Обновлять минимальный набор API/WEB/poller по утверждённому deployment plan. Не пересоздавать PostgreSQL, volumes и несвязанные интеграции.
7. Проверить health, конкретный HTTP flow, логи и rollback criteria; зафиксировать SHA и фактические сервисы.

Не запускайте generic `docker compose up/down/restart` в рабочем каталоге: Compose config зависит от выбранных overlays и локальных env/secrets. В этой задаче deployment не выполнялся.
