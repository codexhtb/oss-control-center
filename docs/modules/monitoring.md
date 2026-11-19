# LibreNMS, Icinga и BDCOM diagnostics

**Назначение.** Read-only наблюдения состояния устройства/интерфейса и ONU/оптических данных в контексте сохранённого `connection_snapshot`.

**Источники/код:** `app/modules/diagnostics/` и `/api/v1/diagnostics`; LibreNMS REST API, опциональный Icinga API, BDCOM ONU reader. BDCOM читает `bdcom_onu_olts` и `bdcom_onus`; при dev tunnel используется отдельный proxy. LibreNMS SNMP collection и custom collector живут вне этого репозитория.

**Mappings.** `MappingRegistry` использует постоянный NetUP switch ID → LibreNMS device ID и отдельные явные port IDs. Автосопоставление допускается только при unique exact sysName согласно коду. Отсутствие mapping не скрывает доступные независимые источники; неизвестные интерфейсы/ONU не угадываются.

**Хранение.** Проверка сохраняется в PostgreSQL ticket diagnostics/events только штатным действием пользователя. BDCOM reader — источник, не система записи. Данные показываются с quality/time/limitations.

**Права:** `diagnostics.read`, запуск требует `diagnostics.run`; endpoint проверяет ticket/lookup visibility. Не обходить permissions прямым вызовом.

**Ошибки:** mapping missing/unconfirmed, stale, source unavailable, multiple candidate, unknown. Не трактуйте `UNKNOWN` как `DOWN` и не связывайте по совпадению account, имени абонента или телефону.

**Runtime status:** наличие provider-кода не подтверждает доступность ключей, API, collectors или актуальность наблюдения. Проверяйте по [LibreNMS runbook](../operations/librenms-integration.md) и health endpoint без печати секретов/данных абонента.

## Корреляция и результат

Подтверждённый device mapping использует стабильные NetUP switch ID и LibreNMS device ID; hostname — только кандидат. Management IP/inventory проверяются read-only. NetUP port нельзя считать LibreNMS `ifIndex` без правила, а ONU требует отдельного однозначного PON/VLAN/OLT ключа.

Ответ сохраняет observation time и quality/reason. LibreNMS device может быть виден при отсутствии ONU; Icinga/BDCOM сообщают собственную причину. Защищённый mapping registry и credentials не коммитятся.
