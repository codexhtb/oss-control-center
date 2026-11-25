# Subscriber cabinet: DEV, release и rollback

## Текущее ограничение

Владелец выполняет ручную сверку памятки через DEV, без файла с реальными
subscriber credentials. До явного подтверждения `SUBSCRIBER_CABINET_MODE`
на рабочем портале остаётся `disabled`. Логин при этом доступен.
На обследованной реплике `have_ssl=DISABLED`; защищённого TLS-канала пока нет.
Проверенная реализация `verified` блокирует выдачу при отсутствии CA, реального
Ssl_cipher или подтверждения памятки. Флаг APP_ENV=dev рабочего портала не
считается основанием для preview: preview требует отдельный HTTPS origin/порт.

## DEV

Отдельный project `oss-cabinet-dev`, база `oss_test_subscriber_cabinet` в
существующем PostgreSQL, адрес `https://172.17.7.2:9443`. Сертификат существующего
DEV CA. Cookie привязаны к IP, а не к домену рабочего портала. Используются
существующие защищённые staff credentials `.dev-secrets/users.json`; seed
даёт право раскрытия dev-engineer/dev-admin только в этой отдельной базе.
DEV использует ту же read-only реплику и явное лабораторное исключение
для SQL-транспорта (`dev_preview`), не выдаёт это за production TLS.
Файлы реквизитов абонентов не создаются.

По запросу владельца пароль `dev-admin` сброшен только в этой DEV-базе.
Новые реквизиты — `.dev-secrets/credentials/subscriber-cabinet-dev-admin.json`,
проверенная локальная копия — `.dev-secrets/backups/subscriber-cabinet-dev-admin.json`.
Файлы 0600, каталоги 0700, metadata внесены в `.dev-secrets/registry.tsv`.
Получение владельцем: `ssh codexdev@172.17.7.2 'cat /opt/oss-control-center/.dev-secrets/credentials/subscriber-cabinet-dev-admin.json'`.
Исходный `users.json` не изменялся; его старый пароль dev-admin больше не действует
на этом стенде. Пароли рабочих пользователей не менялись. Внешнее резервное
копирование нового файла не подтверждено; `/root/oss-secrets/` не использовался
из-за отсутствия доступа у codexdev.

Запуск после создания отдельной пустой базы:

```sh
docker compose --env-file .env.dev -f dev/compose.subscriber-cabinet.yaml run --rm --no-deps cabinet_api alembic upgrade head
docker compose --env-file .env.dev -f dev/compose.subscriber-cabinet.yaml up -d --wait
```

Ручная проверка: войти как dev-engineer, открыть согласованную тестовую запись,
сравнить логин, раскрыть пароль и сравнить с памяткой. Проверить копирование и
скрытие. Сам секрет не присылать в чат/логи; достаточно результата сравнения.
Preview после проверки остановить отдельным `compose down`; рабочие контейнеры
это не затрагивает. DEV-базу удалять отдельно после завершения приёмки.

## Gates и promotion

1. Ruff изменённых Python, diff check, полный pytest на disposable databases,
   frontend typecheck/Vitest/build, Chromium на DEV с синтетическими API fixtures.
2. Те же неизменяемые images проверить на DEV. Миграцию OCC 0054 проверить
   upgrade/downgrade на disposable PostgreSQL. NetUP миграций нет.
3. Commit/push, сохранить старые API/WEB images и защищённый PostgreSQL dump;
   проверить восстановление в отдельную disposable DB.
4. Применить OCC 0054. Она расширяет constraint персональных permissions,
   никому не выдаёт право и не меняет SubscriberSnapshot.
5. Promotion только api/web проекта `oss-control-dev` с полным исходным набором
   compose overlays из [штатного release workflow](notification-center-release.md).
   Не перезапускать postgres, poller, proxy и legacy project.
6. Проверить health, HTTPS и границы 443/8443, новые маршруты без сессии,
   disabled mode рабочего API и неизменность контейнеров остальных сервисов.

Promotion выполняет `dev/deploy_subscriber_cabinet.sh <tested-pushed-commit>`.
Он проверяет соответствие DEV image IDs, восстанавливает backup в отдельную
базу, проверяет upgrade/downgrade и сохранность grants/заявок на этой копии,
затем обновляет только API/WEB. При ошибке live checks автоматически возвращает
предыдущую схему permission и сохранённые images.

Live HTTPS smoke использует минимальные временные staff accounts для трёх
ролей и ранее сохранённые owner-held smoke passwords из
`.dev-secrets/credentials/notification-center-smoke.json`. Файл не изменяется,
новые значения секретов не создаются. Accounts создаются и мягко удаляются
штатным identity service с аудитом, сессии отзываются в finally. Существующие
аккаунты и пароли не меняются. Subscriber lookup/password live smoke не читает.

Имена сохранённых rollback images:
`oss-control-dev-api:pre-cabinet-20261125`,
`oss-control-dev-web:pre-cabinet-20261125`.

При провале: сначала новым image выполнить `alembic downgrade
0053_task_replies_reminders`, затем вернуть оба сохранённых image и выполнить
штатный `up -d --no-deps --wait api web` с теми же overlays. Downgrade удаляет
только новый permission, поскольку прежний код не распознаёт его; все остальные
grants и данные сохраняются. Backup поверх рабочих данных не восстанавливать.

## Будущее production-включение

После ручного подтверждения памятки администратору инфраструктуры необходимо
обеспечить TLS на реплике с доверенной CA и сертификатом, содержащим имя
SQL endpoint, передать защищённо CA и runtime config. Схему/данные/репликацию
не менять; приватные ключи сервера в OCC не передавать. Альтернативный
защищённый туннель требует отдельной проверки и явной поддержки в адаптере —
текущая реализация не принимает объявленный словами «защищённый» plaintext.

Проверить соединение с верификацией CA/имени и непустым Ssl_cipher без чтения
subscriber credentials. В runtime нужны `NETUP_TLS_CA`, production-purpose
NetupConfig и монтирование CA для API. Перед выдачей нового права обновить
Telegram worker до версии, распознающей `subscribers.password.read`:
при текущем promotion старый worker сохраняется, а выдача права через HTTP
в режиме disabled блокируется с 409. Затем установить
`SUBSCRIBER_CABINET_MODE=verified`,
`SUBSCRIBER_CABINET_MEMO_VERIFIED=true` и выдать конкретным сотрудникам
`subscribers.password.read` через штатный редактор с аудитом. Само значение
пароля абонента никуда не сохранять. До выполнения этих условий выдача
паролей на PROD остаётся выключенной.

Предпочтительное хранилище инфраструктурных секретов владельца —
`/root/oss-secrets/`; codexdev не обходит его права. Любые будущие TLS private
keys создавать по [secret-management.md](secret-management.md), с реестром
владельца и проверенной зашифрованной резервной копией.

## Проверки реализации 25.11.2026

Полный backend: 311 passed, 9 skipped (внешние LAB-интеграции).
Последний targeted прогон после защиты grants: 27 passed; frontend полный
223 passed, повторные targeted 9 passed, typecheck и runtime build успешны.
Chromium DEV проверил engineer/operator/admin с синтетическим абонентом:
ленивый POST, CSRF, копирование, скрытие и RBAC. Новый dev-admin пароль
проверен HTTPS-входом; пароли остальных существующих staff accounts не менялись.
Миграция OCC проверена upgrade/downgrade на disposable PostgreSQL.
Реальные реквизиты абонентов автоматические проверки не запрашивают.

Первый promotion автоматически откатился: live smoke унаследовал прежний
DEV origin из `.env.dev` и был остановлен собственным guard до создания
проверочных staff accounts. Схема 0053 и прежние API/WEB восстановлены,
HTTPS readiness прошёл. Runner теперь явно получает production origin;
его configuration gate проверен отдельно до повторного promotion.
