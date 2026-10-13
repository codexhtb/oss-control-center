-- Aggregate-only checks for the service-link -> IP-group-row relation.
SELECT COUNT(*) AS live_ip_links,
       SUM(row_count>1) AS links_with_multiple_rows,
       MAX(row_count) AS max_rows_per_link
FROM (
  SELECT isl.id,COUNT(g.id) AS row_count
  FROM service_links sl JOIN services_data sd ON sd.id=sl.service_id
  JOIN iptraffic_service_links isl ON isl.id=sl.id AND isl.is_deleted=0
  LEFT JOIN ip_groups g ON g.ip_group_id=isl.ip_group_id AND g.is_deleted=0
  WHERE sl.is_deleted=0 AND sd.service_type=3
  GROUP BY isl.id
) x;

SELECT ip_type,COUNT(*) AS live_rows,
       SUM(ip<>0) AS with_ip,SUM(mask<>0) AS with_mask,
       SUM(ip_ext<>0) AS with_ip_ext,SUM(mask_ext<>0) AS with_mask_ext,
       SUM(pool_id>0) AS with_pool_id,SUM(pool_name<>'') AS with_pool_name,
       SUM(switch_id>0 AND port_id>0 AND vlan_id>0) AS with_complete_topology,
       SUM(ip<>0 AND pool_id>0) AS same_row_address_and_pool
FROM ip_groups WHERE is_deleted=0 GROUP BY ip_type ORDER BY ip_type;

SELECT SUM((ip<>0)<>(mask<>0)) AS ip_mask_presence_mismatch,
       SUM((switch_id>0)<>(port_id>0) OR
           (switch_id>0)<>(vlan_id>0)) AS topology_presence_mismatch,
       SUM(pool_name<>'' AND pool_id>0) AS named_rows_with_numeric_pool,
       SUM(pool_name<>'') AS named_rows
FROM ip_groups WHERE is_deleted=0;

SELECT COUNT(*) AS live_rows,
       SUM(g.pool_id>0 AND dpi.id IS NOT NULL AND dpi.is_deleted=0) AS live_pool_matches,
       SUM(g.pool_id>0 AND dpi.id IS NULL) AS missing_pool_matches,
       SUM(g.pool_id>0 AND dpr.pool_id IS NOT NULL) AS live_range_matches
FROM ip_groups g LEFT JOIN dhcp_pool_info dpi ON dpi.id=g.pool_id
LEFT JOIN (SELECT DISTINCT pool_id FROM dhcp_pool_ranges WHERE is_deleted=0) dpr
  ON dpr.pool_id=g.pool_id
WHERE g.is_deleted=0;

WITH groups AS (
  SELECT ip_group_id,COUNT(*) AS row_count,
         SUM(ip_type=0) AS type0_rows,SUM(ip_type=1) AS type1_rows,
         SUM(ip_type=4) AS type4_rows,SUM(pool_id>0) AS pool_rows,
         SUM(ip_type IN (0,1) AND ip<>0) AS option_address_rows,
         COUNT(DISTINCT CASE WHEN switch_id>0 AND port_id>0 AND vlan_id>0
           THEN CONCAT(switch_id,'/',port_id,'/',vlan_id) END) AS topology_variants,
         COUNT(DISTINCT CASE WHEN ip_type=0 AND switch_id>0 AND port_id>0 AND vlan_id>0
           THEN CONCAT(switch_id,'/',port_id,'/',vlan_id) END) AS type0_topology_variants
  FROM ip_groups WHERE is_deleted=0 GROUP BY ip_group_id
)
SELECT COUNT(*) AS groups_total,
       SUM(row_count>1) AS multirow_groups,
       SUM(type0_rows>0 AND type4_rows>0) AS type0_and_type4,
       SUM(type1_rows>0 AND type4_rows>0) AS type1_and_type4,
       SUM(pool_rows>0 AND type4_rows>0) AS pool_and_type4,
       SUM(pool_rows>0 AND option_address_rows>0) AS pool_and_option_address,
       SUM(topology_variants>1) AS multiple_topologies,
       SUM(type0_topology_variants>1) AS multiple_topologies_within_type0
FROM groups;

WITH conflict_keys AS (
  SELECT ip_group_id FROM ip_groups WHERE is_deleted=0 GROUP BY ip_group_id
  HAVING COUNT(DISTINCT CASE WHEN switch_id>0 AND port_id>0 AND vlan_id>0
    THEN CONCAT(switch_id,'/',port_id,'/',vlan_id) END)>1
), profile AS (
  SELECT g.ip_group_id,
         COUNT(DISTINCT CASE WHEN g.pool_id>0 THEN g.pool_id END) AS pool_variants,
         COUNT(DISTINCT CASE WHEN g.ip<>0 THEN CONCAT(g.ip,'/',g.mask) END) AS address_variants,
         COUNT(DISTINCT CASE WHEN g.pool_id>0 THEN CONCAT(g.switch_id,'/',g.port_id,'/',g.vlan_id) END) AS pool_topology_variants,
         SUM(g.is_deleted<>0) AS deleted_rows,
         SUM(g.is_deleted=0 AND (g.create_date<>0 OR g.delete_date<>0)) AS dated_live_rows
  FROM conflict_keys ck JOIN ip_groups g ON g.ip_group_id=ck.ip_group_id
  GROUP BY g.ip_group_id
)
SELECT COUNT(*) AS multi_topology_groups,
       SUM(pool_variants>1) AS multiple_pools,
       SUM(address_variants>1) AS multiple_addresses,
       SUM(pool_topology_variants>1) AS multiple_pool_topologies,
       SUM(deleted_rows>0) AS groups_with_deleted_rows,
       SUM(dated_live_rows>0) AS groups_with_dated_live_rows
FROM profile;
