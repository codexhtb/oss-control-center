-- Aggregate-only IP-group/network checks. Never select ip, mac, device names,
-- credentials, or a subscriber-specific switch/port/VLAN tuple here.

SELECT COUNT(*) AS live_ip_group_rows,
       COUNT(DISTINCT ip_group_id) AS live_group_ids,
       SUM(switch_id>0) AS rows_with_switch,
       SUM(port_id>0) AS rows_with_port,
       SUM(vlan_id>0) AS rows_with_vlan,
       SUM(ip<>0) AS rows_with_ip,
       SUM(mac<>'') AS rows_with_mac
FROM ip_groups WHERE is_deleted=0;

SELECT COUNT(*) AS live_group_ids,
       SUM(n_switch=0) AS no_switch,
       SUM(n_switch>1) AS conflicting_switch,
       SUM(n_port=0) AS no_port,
       SUM(n_port>1) AS conflicting_port,
       SUM(n_vlan=0) AS no_vlan,
       SUM(n_vlan>1) AS conflicting_vlan,
       SUM(n_tuple=1) AS one_complete_tuple,
       SUM(n_tuple>1) AS conflicting_tuples
FROM (
  SELECT ip_group_id,
         COUNT(DISTINCT NULLIF(switch_id,0)) AS n_switch,
         COUNT(DISTINCT NULLIF(port_id,0)) AS n_port,
         COUNT(DISTINCT NULLIF(vlan_id,0)) AS n_vlan,
         COUNT(DISTINCT CASE
           WHEN switch_id>0 AND port_id>0 AND vlan_id>0
           THEN CONCAT(switch_id,'/',port_id,'/',vlan_id)
         END) AS n_tuple
  FROM ip_groups WHERE is_deleted=0 GROUP BY ip_group_id
) g;

SELECT COUNT(*) AS live_group_rows,
       SUM(g.switch_id>0) AS nonzero_switch_ref,
       SUM(g.switch_id>0 AND s.id IS NOT NULL AND s.is_deleted=0)
           AS matching_live_switch,
       SUM(g.switch_id>0 AND s.id IS NULL) AS missing_switch
FROM ip_groups g LEFT JOIN switches s ON s.id=g.switch_id
WHERE g.is_deleted=0;

SELECT COUNT(*) AS live_group_rows,
       SUM(ip<0) AS negative_numeric_ip,
       SUM(ip_ext<>0) AS extended_ip_present,
       SUM(mac<>'') AS mac_present
FROM ip_groups WHERE is_deleted=0;

SELECT TABLE_NAME, COLUMN_NAME
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA='UTM5' AND
  (COLUMN_NAME LIKE '%onu%' OR COLUMN_NAME LIKE '%olt%'
   OR COLUMN_NAME LIKE '%epon%' OR COLUMN_NAME LIKE '%gpon%'
   OR COLUMN_NAME LIKE '%serial%')
ORDER BY TABLE_NAME, COLUMN_NAME;
