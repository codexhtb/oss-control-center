-- Aggregate-only service/catalog/tariff checks. Restore time is known only
-- to the day; the period check uses the entire UTC day 2026-10-10.

SELECT sd.service_type, COUNT(*) AS links_total,
       SUM(sl.is_deleted=0) AS links_not_deleted,
       SUM(sl.is_deleted=0 AND sd.is_deleted=0
           AND u.is_deleted=0 AND a.is_deleted=0 AND ua.is_deleted=0) AS all_live
FROM service_links sl
JOIN services_data sd ON sd.id=sl.service_id
JOIN users u ON u.id=sl.user_id
JOIN accounts a ON a.id=sl.account_id
LEFT JOIN users_accounts ua ON ua.uid=sl.user_id
  AND ua.account_id=sl.account_id
GROUP BY sd.service_type ORDER BY sd.service_type;

SELECT COUNT(*) AS live_links,
       SUM(psl.id IS NOT NULL) AS periodic_record_found,
       SUM(psl.is_deleted=0) AS periodic_not_deleted,
       SUM(psl.is_blocked<>0) AS periodic_blocked,
       SUM(psl.is_planned<>0) AS planned,
       SUM(psl.need_del<>0) AS pending_delete,
       SUM(psl.start_date<=UNIX_TIMESTAMP('2026-10-10 00:00:00')
           AND psl.expire_date>UNIX_TIMESTAMP('2026-10-10 23:59:59'))
           AS spans_restore_day
FROM service_links sl
LEFT JOIN periodic_service_links psl ON psl.id=sl.id
WHERE sl.is_deleted=0;

SELECT COUNT(*) AS active_ip_links,
       SUM(isl.id IS NOT NULL AND isl.is_deleted=0) AS live_ip_subtype,
       COUNT(DISTINCT isl.ip_group_id) AS distinct_ip_group_refs
FROM service_links sl
JOIN services_data sd ON sd.id=sl.service_id AND sd.service_type=3
LEFT JOIN iptraffic_service_links isl ON isl.id=sl.id
WHERE sl.is_deleted=0;

SELECT COUNT(*) AS active_service_links,
       SUM(sl.tariff_link_id>0) AS tariff_link_reference_present,
       SUM(atl.id IS NOT NULL AND atl.account_id=sl.account_id
           AND atl.is_deleted=0) AS same_account_live_tariff_link,
       SUM(sd.tariff_id=atl.tariff_id) AS catalog_tariff_matches_link
FROM service_links sl
JOIN services_data sd ON sd.id=sl.service_id
LEFT JOIN account_tariff_link atl ON atl.id=sl.tariff_link_id
WHERE sl.is_deleted=0;

SELECT COUNT(*) AS contracts_rows FROM contracts;
