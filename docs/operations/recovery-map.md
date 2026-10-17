# OSS Control Center — инвентаризация восстановления

**Статус:** карта зависимостей на 17.10.2026, **не** готовая безусловная процедура disaster recovery. Приведённые источники проверены частично; фактические restore-доказательства обязательны до заявления «можно восстановить с нуля».

## 1. Что можно получить из GitHub

- Backend FastAPI, frontend React/Caddy, Alembic schema и тесты.
- Dev Compose definitions: `compose.dev.yaml`, `compose.monitoring.dev.yaml`, `compose.bdcom.dev.yaml`, `compose.telegram.dev.yaml`.
- OSS adapters для NetUP read-only, LibreNMS API, BDCOM SQL и Telegram T1; код локального BDCOM Unix-socket proxy.
- Runbooks и примеры конфигурации, в которых **нет реальных секретов**.

`git clone` **не** восстанавливает базу данных с заявками, список пользователей и сессии, секреты, сертификаты, approved equipment mappings, systemd SSH-туннель и LibreNMS custom collectors на другом сервере.

## 2. Какие дополнительные артефакты нужны

| Контур | Артефакт | Где существует | Состояние |
| --- | --- | --- | --- |
| OSS dev | PostgreSQL `oss_control_dev` с ticket/callback/field-dispatch records | Docker named volume / pg_dump | Наличие рабочих данных подтверждено; off-host restore не подтверждён |
| OSS dev | `.env.dev`, сертификаты, защищённые runtime credentials | dev VM / владелец секретов | В Git отсутствуют по замыслу; проверить шифрованный backup |
| OSS dev | Одобренные NetUP ↔ LibreNMS device/OLT mapping registry | `.dev-secrets/` на dev VM | В Git не публикуется; без него сопоставления становятся UNKNOWN |
| OSS dev | `oss-librenms-tunnel.service`, SSH auth и proxy socket setup | host dev + Compose | Файл службы и приватные ключи не входят в Git по умолчанию |
| LibreNMS | MariaDB custom tables и их DDL/индексы | LibreNMS host | Две таблицы читаются OSS; полный DDL и off-host restore отдельно проверить |
| LibreNMS | PHP BDCOM collectors + VLAN script + wrapper | `/opt/librenms/local/` | Шесть untracked файлов; Git upstream их не сохраняет |
| LibreNMS | Custom view/plugin | `resources/views/overview/custom/`; исторический `app/Plugins/BdcomOnu/` | View игнорируется Git; остальные пути проверить |
| LibreNMS | systemd services/timers и drop-ins | `/etc/systemd/system/` | Не находятся в OSS Git |
| LibreNMS | Oxidized scripts/model/node list + configuration Git storage | `/usr/local/sbin/`, `/home/oxidized/` | Часть может содержать пароли и конфиги устройств; не публиковать сырьём |
| LibreNMS | RRD/time-series, ONU event/optical history | LibreNMS storage/DB | Отдельная политика резервирования/ретенции |
| NetUP Lab | Восстановленная копия UTM5 (если нужна повторяемость лаборатории) | Отдельный Lab сервер | Не переносится в Git; требует доступа и согласованного backup |
| Telegram | Бот и его token, chat authorization | владелец / Telegram | Токен ротировать/хранить отдельно; chat ID не даёт права на работу бота |

**Осторожно:** в историческом `docs/operations/dev-lab.md` могут быть устаревшие версии стеков и шаги bootstrap. Не запускать `seed` на непустой БД, `docker compose down -v`, пересоздание Postgres volume, миграцию без backup и проверенного restore.

## 3. Порядок безопасного восстановления (план для отдельной изоляции)

1. **Зафиксировать источник.** Текущий коммит OSS, версии ОС/LibreNMS/MariaDB/PHP, список unit и hashes custom-скриптов; отдельно inventory секретов без вывода значений.
2. **Создать защищённые backup комплекты.** PostgreSQL `pg_dump -Fc` с шифрованием и off-host копией; доказать restore в `oss_test_*`, сверить `alembic_version`, количество записей и ключевые внешние зависимости.
3. **Собрать пакет LibreNMS extensions.** Скопировать собственные исходники, plugin/view и systemd units в временную закрытую область. Провести secret scan перед версионированием. Для схемы БД сохранять DDL без клиентских строк.
4. **Восстановить на отдельном test-контуре**, без production SNMP, NetUP и Telegram отправок. Проверить PHP syntax/unit definitions, структуру таблиц, API connectivity и тестовую ONU-карточку из синтетических данных.
5. **Подключить источники по одному:** NetUP Lab read-only → LibreNMS HTTP API → BDCOM SQL по SSH/Unix socket → Telegram только с контролируемой синтетической отправкой. Каждый шаг должен явно давать `UNAVAILABLE`/`UNKNOWN`, если источник не настроен.
6. **Сверить инварианты:** одобренные mappings, качество данных, статусы заявок, история callbacks, отсутствие дублей Telegram, права пользователей, метки времени оптики.
7. **Зафиксировать доказательства DR** (дата, версии, восстановленные артефакты, тесты, проблемы и RTO/RPO после измерения). Без этого слово «полное восстановление» преждевременно.

Это последовательность для будущего отдельного стенда; **не команды для выполнения на работающем сервере**.

## 4. Минимальный read-only audit checklist

- `git status` и `git rev-parse HEAD` у OSS и upstream LibreNMS — отдельно.
- `systemctl cat` каждого BDCOM/SSH/Oxidized unit **после проверки отсутствия секретов**.
- Хеши/sizes собственных collector scripts; `git ls-files` vs `git status --untracked-files=all`.
- `SHOW CREATE TABLE` для custom таблиц; списки индексов, grants read-only user — без credentials.
- Проверка состояния Compose и health OSS без дампов env.
- Состав и шифрование off-host backup, документированный **test restore**.

**Не включать в Git:** SNMP community, Telegram bot token, ключи SSH/CA, subscriber contacts/descriptions, SQL dumps, приватные Oxidized configs, RRD и журналы с чувствительными полями.

## 5. Текущие пробелы

- Полный текущий PHP-код collector, его зависимости и версия сопоставлений после сентября ещё не проходили source review.
- Нет подтверждённой инвентаризации custom LibreNMS plugin и exact `SHOW CREATE TABLE` по всем таблицам.
- Нет проверенного полного off-host DR от LibreNMS + OSS с сохранением рабочих заявок.
- Icinga integration не включена в runtime; NetUP production требует отдельного проекта и согласования.

Ссылки: [dev-lab](dev-lab.md), [Bdcom SQL reader](bdcom-onu-readonly.md), [security](secret-management.md), [BDCOM architecture](../integrations/librenms-bdcom.md).
