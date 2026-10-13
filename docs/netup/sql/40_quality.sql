-- Aggregate-only quality checks. No traffic/session history scans.

SELECT COUNT(*) AS live_accounts,
       SUM(a.is_blocked<>0) AS account_flagged,
       SUM(b.account_id IS NOT NULL) AS has_active_block_row,
       SUM(a.is_blocked<>0 AND b.account_id IS NOT NULL) AS both_marked,
       SUM(a.is_blocked<>0 AND b.account_id IS NULL) AS flag_only,
       SUM(a.is_blocked=0 AND b.account_id IS NOT NULL) AS block_row_only
FROM accounts a
LEFT JOIN (
  SELECT DISTINCT account_id FROM blocks_info
  WHERE is_deleted=0 AND is_planning=0
    AND start_date<=UNIX_TIMESTAMP('2026-10-10 23:59:59')
    AND (expire_date IS NULL OR expire_date=0
         OR expire_date>UNIX_TIMESTAMP('2026-10-10 23:59:59'))
) b ON b.account_id=a.id
WHERE a.is_deleted=0;

SELECT COUNT(*) AS active_ip_links,
       SUM(a.is_blocked<>0) AS account_blocked_flag,
       SUM(psl.is_blocked<>0) AS service_periodic_blocked
FROM service_links sl
JOIN services_data sd ON sd.id=sl.service_id AND sd.service_type=3
JOIN accounts a ON a.id=sl.account_id
JOIN periodic_service_links psl ON psl.id=sl.id
WHERE sl.is_deleted=0;

SELECT COUNT(*) AS live_users,
       SUM((mobile_telephone<>'')+(work_telephone<>'')
           +(home_telephone<>'')=0) AS no_phone,
       SUM((mobile_telephone<>'')+(work_telephone<>'')
           +(home_telephone<>'')>1) AS multiple_phone_fields
FROM users WHERE is_deleted=0;

SELECT COUNT(*) AS leases,
       SUM(expired>UNIX_TIMESTAMP('2026-10-10 23:59:59'))
           AS unexpired_asof_restore
FROM dhcp_leases;

SELECT COUNT(*) AS empty_technical_params FROM slink_tech_param;
