# ADR-002: Внешние интеграции read-only

**Статус:** принято по текущей реализации. **Дата фиксации:** 2026-11-18.

## Решение

NetUP BillingProvider и LibreNMS/BDCOM diagnostic readers получают данные извне, не выполняя бизнес-записи в эти системы. NetUP adapter использует bound SQL parameters, timeout и read-only consistent transaction; monitoring provider формирует observations.

## Ограничения

External grants, upstream replication, collectors и source availability принадлежат своим владельцам и не доказываются кодом OCC. Icinga optional. Отсутствующие данные не заменяются предположениями.

## Мотивация

Исходная историческая мотивация зафиксирована не полностью; ADR не реконструирует её.
