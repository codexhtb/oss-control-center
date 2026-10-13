-- Aggregate-only identity/account checks on the 2026-10-10 UTM5 lab restore.
-- Do not project names, account IDs, contacts, addresses, or credentials.

SELECT COUNT(*) AS users_total, SUM(is_deleted=0) AS users_live,
       SUM(is_juridical<>0 AND is_deleted=0) AS live_juridical
FROM users;

SELECT COUNT(*) AS accounts_total, SUM(is_deleted=0) AS accounts_live,
       SUM(account_name='') AS blank_account_name,
       SUM(is_deleted=0 AND external_id<>'') AS live_external_id_present
FROM accounts;

SELECT COUNT(*) AS links_total, SUM(ua.is_deleted=0) AS links_live,
       SUM(u.id IS NULL) AS missing_user,
       SUM(a.id IS NULL) AS missing_account,
       SUM(ua.is_deleted=0 AND u.is_deleted=0 AND a.is_deleted=0) AS all_live
FROM users_accounts ua
LEFT JOIN users u ON u.id=ua.uid
LEFT JOIN accounts a ON a.id=ua.account_id;

SELECT COUNT(*) AS live_users,
       SUM(a.id IS NOT NULL AND a.is_deleted=0) AS live_basic_account,
       SUM(ua.id IS NOT NULL) AS live_basic_account_link
FROM users u
LEFT JOIN accounts a ON a.id=u.basic_account
LEFT JOIN users_accounts ua ON ua.uid=u.id
  AND ua.account_id=u.basic_account AND ua.is_deleted=0
WHERE u.is_deleted=0;

SELECT COUNT(*) AS live_users,
       SUM(u.login REGEXP '^[0-9]+$') AS numeric_login,
       SUM(u.login=CAST(a.id AS CHAR)) AS login_equals_account_id,
       COUNT(DISTINCT u.login) AS distinct_logins
FROM users u JOIN accounts a ON a.id=u.basic_account
WHERE u.is_deleted=0 AND a.is_deleted=0;

SELECT COUNT(*) AS live_users,
       SUM(full_name IS NOT NULL AND TRIM(full_name)<>'') AS name_present,
       SUM(mobile_telephone<>'') AS mobile_present,
       SUM(work_telephone<>'') AS work_present,
       SUM(home_telephone<>'') AS home_present,
       SUM(email<>'') AS email_present,
       SUM(actual_address IS NOT NULL AND TRIM(actual_address)<>'') AS address_present,
       SUM(CHAR_LENGTH(mobile_telephone)>50) AS mobile_over_current_limit,
       SUM(CHAR_LENGTH(work_telephone)>50) AS work_over_current_limit
FROM users WHERE is_deleted=0;
