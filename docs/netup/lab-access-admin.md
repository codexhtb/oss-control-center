# Действия администратора для отдельного lab runtime-канала

Текущее состояние на `oss-control-dev`: TCP `172.17.7.1:3306` закрыт,
`codexdev` не имеет доступа к Docker socket. Эти ограничения агент не
обходит. Ниже команды относятся **только** к `netup-db-lab`, восстановленной
копии UTM5. Production NetUP не затрагивать.

## Диагностика на `netup-db-lab`

```bash
sudo ss -ltnp '( sport = :3306 )'
sudo mariadb -NBe "SHOW VARIABLES WHERE Variable_name IN ('bind_address','port','skip_networking')"
```

Если MariaDB слушает только loopback, на Debian/Ubuntu lab VM администратор
может выполнить следующие действия (после проверки фактического пути
конфигурации и активного firewall):

```bash
sudoedit /etc/mysql/mariadb.conf.d/50-server.cnf
# В секции [mysqld] установить: bind-address = 172.17.7.1
sudo systemctl restart mariadb
sudo ss -ltnp '( sport = :3306 )'
sudo ufw status
# Только если UFW активен:
sudo ufw allow from 172.17.7.2 to 172.17.7.1 port 3306 proto tcp
```

Если активен иной firewall, администратор добавляет эквивалентное правило
только для `172.17.7.2`. Лабораторный провайдер продолжает использовать
read-only SQL user, хотя владелец разрешил отдельные эксперименты со схемой
копии. Индексы и миграции для экспериментов не требуются для работы адаптера.

## Отдельный lab user

Открыть `sudo mariadb` интерактивно и создать отдельную учётную запись
`'oss_lab_ro'@'172.17.7.2'` с уникальным секретом, переданным через
защищённый канал. Секрет не записывать в Git, shell history или этот файл.
Дать только **column-level SELECT** на поля, используемые SQL repository:

```sql
CREATE USER 'oss_lab_ro'@'172.17.7.2' IDENTIFIED BY '<unique lab secret>';
GRANT SELECT (id,is_deleted) ON UTM5.accounts TO 'oss_lab_ro'@'172.17.7.2';
GRANT SELECT (id,basic_account,full_name,is_juridical,mobile_telephone,
  work_telephone,home_telephone,email,actual_address,is_deleted)
  ON UTM5.users TO 'oss_lab_ro'@'172.17.7.2';
GRANT SELECT (uid,account_id,is_deleted)
  ON UTM5.users_accounts TO 'oss_lab_ro'@'172.17.7.2';
GRANT SELECT (id,user_id,account_id,service_id,is_deleted)
  ON UTM5.service_links TO 'oss_lab_ro'@'172.17.7.2';
GRANT SELECT (id,service_name,service_type,is_deleted)
  ON UTM5.services_data TO 'oss_lab_ro'@'172.17.7.2';
GRANT SELECT (id,is_deleted,is_planned,need_del,start_date,expire_date)
  ON UTM5.periodic_service_links TO 'oss_lab_ro'@'172.17.7.2';
GRANT SELECT (id,ip_group_id,is_deleted)
  ON UTM5.iptraffic_service_links TO 'oss_lab_ro'@'172.17.7.2';
GRANT SELECT (id,ip_group_id,ip_type,ip,mask,ip_ext,mask_ext,pool_id,
  switch_id,port_id,vlan_id,is_deleted)
  ON UTM5.ip_groups TO 'oss_lab_ro'@'172.17.7.2';
GRANT SELECT (id,name,is_deleted)
  ON UTM5.switches TO 'oss_lab_ro'@'172.17.7.2';
GRANT SELECT (id,is_deleted)
  ON UTM5.dhcp_pool_info TO 'oss_lab_ro'@'172.17.7.2';
GRANT SELECT (id,pool_id,first_ip,last_ip,is_deleted)
  ON UTM5.dhcp_pool_ranges TO 'oss_lab_ro'@'172.17.7.2';
SHOW GRANTS FOR 'oss_lab_ro'@'172.17.7.2';
```

Лаборатория допускает временный plaintext на private IP по явному флагу;
production runtime требует TLS CA и проверки имени. После настройки
проверить с `oss-control-dev` только доступность порта, затем положить
реквизиты в локальный игнорируемый `.env.netup-lab` с правом `0600`.
Значения `NETUP_LAB_ACCOUNT_NO` и сценарных ЛС задавать только там, не в Git.

После этого агент может запустить `RUN_NETUP_LAB_TESTS=1` и реальные
integration tests через PyMySQL, без Docker и без вывода абонентских строк.
Команда на `oss-control-dev` после выдачи доступа:

```bash
cd /opt/oss-control-center
chmod 600 .env.netup-lab
set -a
. ./.env.netup-lab
set +a
.venv/bin/pytest -q tests/test_netup.py -m integration
```

Файл `.env.netup-lab` игнорируется Git; команда не печатает его содержимое.
