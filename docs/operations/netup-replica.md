# NetUP replica operations

OCC NetUP adapter использует параметризованные read-only запросы в consistent snapshot, ограничивает statement time/results и сохраняет нормализованные snapshots в PostgreSQL OCC. В коде нет replication agent и нет команд записи в NetUP.

## Read-only подтверждения

- Проверяйте billing provider mode по конфигурационному inventory без вывода значений.
- Проверяйте API readiness отдельно от доступности NetUP: readiness не обещает успешный subscriber lookup.
- DBA подтверждает replica lag, source/replica role, read-only grants, TLS, replication health и время последнего sync своим read-only мониторингом.
- В случае mismatch сохраните source quality/error code; не изменяйте NetUP и не подменяйте snapshot.

Upstream replication schedule/technology, replica host/VM placement и восстановление replica **UNVERIFIED**. Подтвердить у NetUP DBA; не использовать discovery SQL/старые LAB recipes как production command.
