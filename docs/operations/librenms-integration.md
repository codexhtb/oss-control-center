# LibreNMS integration

OCC читает LibreNMS через configured API provider; устройства и порты связываются с NetUP только через утверждённый mapping registry. BDCOM ONU observations — отдельный reader и данные collector. Icinga создаётся только при полной конфигурации URL/user/protected password file.

## Проверка

1. Убедиться по inventory имён переменных и наличию protected mount, не показывая значения или содержимое secret.
2. Проверить health API и актуальность observation timestamp/quality в тестовом, согласованном контексте.
3. Сверить mapping basis и постоянные IDs в защищённом локальном registry; не копировать mapping в Git/issues.
4. Сверить collector health в LibreNMS/BDCOM system и источник времени.
5. При отсутствии evidence показывать UNKNOWN/UNAVAILABLE; не связывать порт по порядковому номеру, VLAN или похожему имени без правила.

SNMP target list, community/auth secrets, collector code, scheduling и модель устройств **UNVERIFIED в репозитории**. Изменение network equipment вне области OCC и требует отдельного владельца.
