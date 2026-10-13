-- Aggregate-only validation. Never selects subscriber/account identifiers or PII.
-- GUI example was checked separately with predicates, without recording IDs here.
SELECT COUNT(*) AS live_associations,
       SUM(ua.account_id=u.basic_account) AS primary_matches,
       SUM(ua.account_id<>u.basic_account) AS secondary_matches
FROM users_accounts ua
JOIN users u ON u.id=ua.uid AND u.is_deleted=0
JOIN accounts a ON a.id=ua.account_id AND a.is_deleted=0
WHERE ua.is_deleted=0;

SELECT COUNT(*) AS users_with_multiple_accounts_ever,
       SUM(live_accounts>1) AS users_with_multiple_live_accounts
FROM (
  SELECT uid, COUNT(DISTINCT account_id) AS all_accounts,
         COUNT(DISTINCT CASE WHEN is_deleted=0 THEN account_id END) AS live_accounts
  FROM users_accounts GROUP BY uid HAVING all_accounts>1
) x;

SELECT COUNT(*) AS live_accounts_without_live_user
FROM accounts a
WHERE a.is_deleted=0 AND NOT EXISTS (
  SELECT 1 FROM users_accounts ua JOIN users u ON u.id=ua.uid
  WHERE ua.account_id=a.id AND ua.is_deleted=0 AND u.is_deleted=0
);

SELECT COUNT(*) AS accounts_with_multiple_live_users
FROM (
  SELECT ua.account_id
  FROM users_accounts ua JOIN accounts a ON a.id=ua.account_id AND a.is_deleted=0
  JOIN users u ON u.id=ua.uid AND u.is_deleted=0
  WHERE ua.is_deleted=0 GROUP BY ua.account_id
  HAVING COUNT(DISTINCT ua.uid)>1
) x;

-- Uses an existing account without exposing its ID or subscriber row.
EXPLAIN SELECT a.id AS account_id, ua.uid AS user_id
FROM accounts a
JOIN users_accounts ua ON ua.account_id=a.id AND ua.is_deleted=0
JOIN users u ON u.id=ua.uid AND u.is_deleted=0
WHERE a.id=(SELECT MIN(id) FROM accounts WHERE is_deleted=0)
  AND a.is_deleted=0 LIMIT 2;
