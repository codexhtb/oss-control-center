# Subscriber cabinet: утверждённое включение в management-сети

Владелец 25.11.2026 отменил обязательный TLS/SSH к MariaDB и явно принял риск
существующего канала `172.17.7.1:3307`. Используется прежний read-only SQL account
из защищённого runtime env. Никакие данные, grants, схема, конфигурация MariaDB
и репликация не изменяются. HTTPS браузер → OCC сохраняется.

## Поведение и права

`compose.subscriber-cabinet-management.yaml` добавляется к полному штатному
набору compose overlays. Он включает `SUBSCRIBER_CABINET_MODE=management`,
production-purpose и явный `NETUP_ALLOW_PLAINTEXT_MANAGEMENT=true`.
Исключение действует только для утверждённого endpoint; перед password SELECT
проверяются `@@server_id=2`, `@@read_only=1`, единственный владелец счёта и
`users.encrypted=0`. Исторический as_of не допускается. Автоматического fallback
на другое подключение нет. Default mode приложения остаётся disabled.

Операторам добавляются персональные `subscribers.read`/`subscribers.password.read`
в одной транзакции с аудитом. Сохраняются остальные права и прежние разрешения
других ролей. Preset новых операторов включает оба права; администратор может
отозвать любое из них обычным редактором. Авторизация, CSRF, повторная проверка
прав после SQL, обязательная фиксация аудита до ответа и no-store не меняются.
Пароль находится только в памяти запроса и состоянии компонента, не в snapshots,
PostgreSQL, логах, React Query, browser storage или тестовых артефактах.

## Проверки и promotion

1. Ruff/diff check, полный backend regression на disposable PostgreSQL,
   frontend typecheck/Vitest/build. Tests используют синтетические credentials.
2. Проверить эти же API/WEB images на изолированном DEV с
   `dev/compose.subscriber-cabinet-management.yaml`. Владельцем ранее открытая
   DEV-запись используется для live приёмки без вывода реквизитов.
3. `sg docker -c '.venv/bin/python dev/run_cabinet_operator_live.py --dev'`:
   временный оператор, настоящий HTTPS API, отдельный POST password, CSRF,
   аудит/no-store/snapshot checks и Chromium reveal/copy/hide. Screenshot,
   trace, video, файлы с исходным паролем не создаются; clipboard очищается.
   Staff password переиспользуется из существующего owner-held smoke manifest,
   передаётся Chromium только через приватный stdin pipe. Временный аккаунт
   удаляется мягко, его сессии отзываются в finally.
4. Commit/push. `dev/deploy_cabinet_management.sh <tested-pushed-commit>`
   проверяет DEV image IDs, сохраняет API/WEB/worker images и PG dump (`0600`),
   восстанавливает dump в отдельную `oss_test_cabinet_management_restore`.
   На копии проверяются выдача grants всем операторам и обратный откат добавлений.
   Новых migrations нет, OCC head остаётся `0054_subscriber_password`.
5. Сначала управляемо обновить Telegram worker на image с новым Permission enum,
   затем API/WEB, затем добавить права. Иначе прежний worker не распознаёт
   сохранённый grant. Offset не сбрасывается: `_offset` читает записи PostgreSQL.
   Token init, postgres, BDCOM proxy и legacy project не перезапускаются.
6. HTTPS readiness, границы 443/8443, права всех операторов, live API и Chromium
   под оператором на PROD. Значения subscriber login/password не выводятся.
   Проверить worker enum/offset и неизменность контейнеров остальных сервисов.

## Rollback

Сохранённые images: `oss-control-dev-api:pre-cabinet-management-20261125`,
`oss-control-dev-web:pre-cabinet-management-20261125`,
`oss-cabinet-worker:pre-management-20261125`.
Автоматический rollback при провале live-проверки останавливает worker,
отзывает только grants, добавленные данным релизом (защищённый JSON receipt),
возвращает прежние API/WEB и disabled mode без management overlay. Worker
запускается на прежнем cabinet API image, который уже понимает Permission enum:
это сохраняет совместимость с возможными одновременными изменениями grants.
Другие права, заявки, snapshots и Telegram offset не восстанавливаются из dump
поверх рабочих данных. Backup сохраняется для восстановления администратором;
его успешный локальный restore не подтверждает off-host backup.

## Секреты и отменённая работа SSH

Новые пароли, SSH-ключи и сертификаты этим релизом не создаются. Незавершённые
SSH код, тесты и compose overlays удалены; SSH services/sshd/authorized_keys
не изменялись. Существующий ключ не ротировался. Созданная администратором
runtime-копия `.dev-secrets/netup-ssh/id_ed25519_netup_ro` и проверенная локальная
копия `.dev-secrets/backups/netup-ssh-id_ed25519_netup_ro` остаются защищёнными
(`0600`, каталог `0700`), не монтируются и не используются. Владелец может
прочесть их как root; исходный `/home/codex/.ssh/id_ed25519_netup_ro` не менялся.
Метаданные записаны в локальный реестр; off-host копия не подтверждена.

## Выполненные проверки

Перед promotion: полный backend 325 passed, 9 skipped (только внешние LAB
интеграции), 18:14; frontend 223 passed, typecheck/build успешны. После уточнения
assertion preset оператора повторный targeted frontend прогон: 17 passed.
Настоящая DEV-приёмка оператора через API и Chromium прошла: исходная маска,
ленивый POST, копирование обоих значений, скрытие, CSRF, no-store и аудит.
Значения реквизитов не выводились; test account удалён, сессии отозваны.
