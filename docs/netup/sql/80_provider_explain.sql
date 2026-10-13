-- Provider query plans only. Subqueries select existing keys without printing them.
EXPLAIN SELECT id FROM accounts
WHERE id=(SELECT MIN(id) FROM accounts WHERE is_deleted=0) AND is_deleted=0;

EXPLAIN SELECT DISTINCT u.id FROM users_accounts ua
JOIN users u ON u.id=ua.uid AND u.is_deleted=0
WHERE ua.account_id=(SELECT MIN(id) FROM accounts WHERE is_deleted=0)
  AND ua.is_deleted=0 ORDER BY u.id LIMIT 2;

EXPLAIN SELECT DISTINCT a.id FROM users_accounts ua
JOIN accounts a ON a.id=ua.account_id AND a.is_deleted=0
WHERE ua.uid=(SELECT MIN(id) FROM users WHERE is_deleted=0)
  AND ua.is_deleted=0 ORDER BY a.id LIMIT 51;

EXPLAIN SELECT sl.id,sl.account_id,sd.service_name,sd.service_type
FROM service_links sl
JOIN services_data sd ON sd.id=sl.service_id AND sd.is_deleted=0
JOIN periodic_service_links psl ON psl.id=sl.id AND psl.is_deleted=0
WHERE sl.user_id=(SELECT MIN(id) FROM users WHERE is_deleted=0)
  AND sl.account_id=(SELECT MIN(id) FROM accounts WHERE is_deleted=0)
  AND sl.is_deleted=0 AND sd.service_type IN (2,3)
  AND psl.is_planned=0 AND psl.need_del=0
  AND psl.start_date<=UNIX_TIMESTAMP() AND psl.expire_date>UNIX_TIMESTAMP()
ORDER BY sl.id LIMIT 51;

EXPLAIN SELECT g.id,g.ip_type,g.ip,g.mask,g.ip_ext,g.mask_ext,
  g.pool_id,g.switch_id,g.port_id,g.vlan_id,s.name
FROM ip_groups g LEFT JOIN switches s ON s.id=g.switch_id AND s.is_deleted=0
WHERE g.ip_group_id=(SELECT MIN(ip_group_id) FROM iptraffic_service_links WHERE is_deleted=0)
  AND g.is_deleted=0 ORDER BY g.id LIMIT 101;
