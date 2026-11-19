> HISTORICAL — обезличенное техническое исследование. Не является разрешением автоматически создавать mappings.

# NetUP UTM5 ↔ LibreNMS: границы сопоставления портов

Исследование сравнивало технические ключи действующих IP-услуг в read-only снимке NetUP с inventory/ports LibreNMS. Конкретные устройства, IP, IDs, объёмы сети и строки абонентов удалены. Сохраняются правила, которые нужны для безопасного сопровождения diagnostics.

## Главный вывод

Универсальное правило `NetUP port_id = LibreNMS ifIndex` неверно. На разных семействах оборудования NetUP port может совпадать с `ifIndex`, соответствовать суффиксу `ifName` со смещением либо обозначать PON, а не абонентский ONU interface. `LibreNMS port_id` — внутренний ID и также не равен номеру NetUP.

Совпадение VLAN на единственном физическом интерфейсе является сильным кандидатом для Ethernet, но не доказательством физической коммутации. Tagged membership слабее untagged/PVID: один VLAN может присутствовать на многих портах. Источники могут относиться к разному времени.

Для PON нельзя выводить ONU из физического порта. Имена вида `EPON/GPON...:ONU`, VLAN и похожий номер требуют подтверждённого OLT, правила преобразования PON и однозначного ключа OLT+PON+VLAN. При нескольких service rows/ONU результат — `CONFLICT`, не первая строка.

## Безопасная классификация интерфейсов

- Ethernet: учитывать `ifType`, исключать VLAN/loopback/virtual/LAG; правило имени зависит от модели.
- PON: физический `EPON0/n`/`GPON0/n` и ONU-подобный `...:m` — разные сущности; одинаковый `ifType` возможен.
- `ifAlias` — свободный потенциально чувствительный текст; не логировать и не сохранять в общий registry.
- `ifAdminStatus`, `ifOperStatus` и device UP/DOWN используются только после подтверждения пары. Они не являются состоянием услуги сами по себе.
- При rediscovery нужно разрешать сохранённый LibreNMS port ID заново и сравнивать контрольные `ifIndex/ifName/ifType`.

## BDCOM observations

Inventory collectors могут сохранять ONU interface, state, reason, presence и `last_seen`; optical collectors — текущие поля Rx/Tx/OLT Rx и время обновления. Эти времена имеют собственную частоту и timezone quality. `present=0` означает отсутствие в последнем успешном наборе, а не доказанное физическое отключение.

Для EPON identity может быть MAC; для GPON legacy-колонка с названием `mac` может фактически содержать нормализованный serial. UI обязан показывать подтверждённый тип идентификатора и не называть serial MAC-адресом.

LibreNMS sensor `dbm`, привязанный к физическому PON/uplink `ifIndex`, не является автоматически оптикой конкретной ONU. Оптическое observation требует связи collector row с подтверждённой ONU и отдельной свежести.

## Модель evidence для port mapping

Рекомендуемые неперсональные поля:

| Группа | Поля/смысл |
| --- | --- |
| Ключ | switch ID, NetUP port ID, LibreNMS device ID, LibreNMS port ID |
| Drift control | `ifIndex`, `ifName`, `ifType`, sysName, hardware на момент проверки |
| Качество | access class, match method, `VERIFIED/INFERRED/CONFLICT/UNKNOWN` |
| Время | evidence time, времена обоих sources, verifier |
| Audit | source revision, review note, `candidate/approved/revoked` |

Не хранить `ifAlias`, subscriber IP/MAC/ФИО/телефон или tokens. Изменение контрольных полей понижает mapping до `UNKNOWN`, а не выбирает новый интерфейс автоматически.

## Воспроизводимость

1. В NetUP отбирать действующие service links параметризованным read-only SQL и считать service rows, IP-group rows и пары switch/port отдельно.
2. Пересекать только с approved device registry; не выполнять hostname fuzzy match внутри абонентского lookup.
3. В LibreNMS запрашивать минимальные columns: port ID, ifIndex/name/type/admin/oper/VLAN и poll time; не получать/логировать auth fields и alias.
4. Для Ethernet применять model-specific rule плюс уникальный VLAN evidence; для PON возвращать `UNKNOWN` до отдельной ONU identity.
5. При многозначности любой стороны возвращать `CONFLICT`.

Первоначальные SQL, инвентарные числа и конкретные mappings не публикуются. Актуальная реализация описана в [модуле monitoring](../../modules/monitoring.md).
