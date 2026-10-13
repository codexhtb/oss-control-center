-- Plans only. MIN(id) picks an existing account without printing its ID.
-- These EXPLAINs return metadata, never subscriber rows.

EXPLAIN SELECT a.id, u.id
FROM accounts a
JOIN users_accounts ua ON ua.account_id=a.id AND ua.is_deleted=0
JOIN users u ON u.id=ua.uid AND u.is_deleted=0
WHERE a.id=(SELECT MIN(id) FROM accounts WHERE is_deleted=0)
  AND a.is_deleted=0;

EXPLAIN SELECT sl.id, sd.service_type, psl.start_date, psl.expire_date
FROM service_links sl
JOIN services_data sd ON sd.id=sl.service_id AND sd.is_deleted=0
LEFT JOIN periodic_service_links psl ON psl.id=sl.id
WHERE sl.account_id=(SELECT MIN(id) FROM accounts WHERE is_deleted=0)
  AND sl.is_deleted=0;

EXPLAIN SELECT isl.ip_group_id, g.id, g.switch_id, g.port_id, g.vlan_id
FROM service_links sl
JOIN services_data sd ON sd.id=sl.service_id AND sd.service_type=3
JOIN iptraffic_service_links isl ON isl.id=sl.id AND isl.is_deleted=0
JOIN ip_groups g ON g.ip_group_id=isl.ip_group_id AND g.is_deleted=0
WHERE sl.account_id=(SELECT MIN(id) FROM accounts WHERE is_deleted=0)
  AND sl.is_deleted=0;
