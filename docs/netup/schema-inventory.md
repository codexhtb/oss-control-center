# UTM5: инвентаризация схемы лабораторной копии

**VERIFIED (2026-10-13):** `UTM5` на MariaDB 10.11.19; 179 базовых таблиц,
все InnoDB. Копия обозначена датой 2026-10-10, точное время backup — **UNKNOWN**.
`information_schema.TABLES.TABLE_ROWS` и кардинальность индексов — оценки,
не точные `COUNT(*)`. Объявленных FOREIGN KEY в `KEY_COLUMN_USAGE` нет.
Сумма оценок данных и индексов — около 4 785 MiB.

Читались только метаданные и агрегаты. Строки с данными абонентов или
секретами не выгружались. Повторяемый SQL:
[`sql/00_schema_inventory.sql`](sql/00_schema_inventory.sql).

## Все таблицы

| Таблица | Оценка строк | Колонки | Индексы |
|---|---:|---:|---:|
| `accounts` | 5192 | 18 | 2 |
| `account_invoice` | 4 | 3 | 1 |
| `account_tariff_link` | 7824 | 7 | 2 |
| `acts` | 808 | 9 | 1 |
| `acts_templates` | 31 | 9 | 1 |
| `additional_ip_zones2houses` | 0 | 3 | 1 |
| `archives` | 0 | 6 | 2 |
| `balance_history` | 671874 | 5 | 1 |
| `banks` | 8 | 5 | 1 |
| `blocks_info` | 275082 | 10 | 4 |
| `block_check_timemarks` | 0 | 3 | 1 |
| `card_info` | 83754 | 10 | 3 |
| `card_info_expired` | 0 | 10 | 1 |
| `card_pool_info` | 15 | 6 | 1 |
| `card_pool_owners` | 0 | 4 | 1 |
| `charge_policy` | 2 | 4 | 1 |
| `cluster_staff` | 0 | 9 | 1 |
| `coefficient_schedule` | 0 | 4 | 1 |
| `coefficient_schedule_entity` | 0 | 5 | 0 |
| `coefficient_schedule_link` | 0 | 9 | 1 |
| `coefficient_scheme` | 0 | 6 | 1 |
| `coefficient_scheme_entity` | 0 | 5 | 0 |
| `contracts` | 0 | 5 | 1 |
| `contracts_templates` | 0 | 5 | 1 |
| `contract_types` | 0 | 5 | 1 |
| `credits` | 32248 | 8 | 1 |
| `currency_list` | 3 | 5 | 1 |
| `currency_rates` | 3 | 4 | 1 |
| `custom_services` | 0 | 8 | 1 |
| `dealer_access_list` | 0 | 5 | 1 |
| `dealer_details` | 0 | 16 | 1 |
| `dealer_log` | 0 | 5 | 1 |
| `dhcp_leases` | 1815 | 9 | 3 |
| `dhcp_leases_log` | 570164 | 7 | 1 |
| `dhcp_options` | 9 | 7 | 1 |
| `dhcp_pool_info` | 5 | 11 | 1 |
| `dhcp_pool_ranges` | 70 | 5 | 1 |
| `dhs_access_log` | 0 | 10 | 1 |
| `dhs_access_log_attrs` | 0 | 7 | 1 |
| `dhs_sessions_detail` | 0 | 10 | 2 |
| `dhs_sessions_log` | 13789924 | 31 | 3 |
| `dhs_sessions_log_attrs` | 0 | 7 | 1 |
| `dialup_cost_info` | 0 | 4 | 1 |
| `dialup_services_data` | 0 | 6 | 1 |
| `dialup_service_links` | 0 | 10 | 1 |
| `dir_zones` | 0 | 3 | 1 |
| `discount_periods` | 4329 | 11 | 2 |
| `discount_transactions_all` | 1416969 | 12 | 3 |
| `discount_transactions_iptraffic_all` | 3180 | 15 | 2 |
| `document_profiles` | 2 | 5 | 1 |
| `document_profile_templates` | 12 | 4 | 1 |
| `document_replacement` | 0 | 3 | 1 |
| `document_storage` | 0 | 10 | 3 |
| `document_templates` | 11 | 7 | 1 |
| `downloaded` | 19032 | 10 | 2 |
| `dtagg_cust` | 2 | 6 | 1 |
| `dtagg_hotspot` | 0 | 8 | 2 |
| `dtagg_iptraffic` | 685850 | 9 | 4 |
| `dtagg_once` | 1885 | 5 | 2 |
| `dtagg_periodic` | 14670 | 5 | 2 |
| `dtagg_telephony` | 0 | 8 | 1 |
| `dtagg_telephony_v2` | 0 | 7 | 2 |
| `dynamic_ip` | 0 | 6 | 1 |
| `dynashape_borders` | 382 | 7 | 1 |
| `dynashape_data` | 513 | 4 | 1 |
| `dynashape_radius_attrs` | 0 | 7 | 1 |
| `dynashape_services` | 392 | 4 | 1 |
| `dynashape_settings` | 109 | 3 | 1 |
| `dynashape_tclasses` | 271 | 4 | 1 |
| `firewall_rules` | 0 | 14 | 1 |
| `firewall_rules_new` | 9 | 10 | 1 |
| `funds_flow_settings` | 0 | 4 | 1 |
| `funds_flow_transactions` | 0 | 6 | 1 |
| `groups` | 15 | 2 | 1 |
| `hotspot_allowed_net` | 0 | 4 | 1 |
| `hotspot_cost_info` | 0 | 3 | 0 |
| `hotspot_network_list` | 0 | 5 | 1 |
| `hotspot_services_data` | 0 | 5 | 1 |
| `hotspot_service_links` | 0 | 8 | 1 |
| `houses` | 4 | 11 | 1 |
| `ic_integration` | 0 | 4 | 2 |
| `ic_settings` | 0 | 15 | 1 |
| `invoices` | 332210 | 22 | 2 |
| `invoice_entry` | 334449 | 14 | 2 |
| `invoice_entry_details` | 1637 | 5 | 2 |
| `iptraffic_borders` | 115 | 4 | 1 |
| `iptraffic_services_data` | 221 | 5 | 1 |
| `iptraffic_service_links` | 18040 | 4 | 2 |
| `iptv_custom_data` | 0 | 2 | 1 |
| `iptv_services_data` | 0 | 6 | 1 |
| `ip_groups` | 6424 | 21 | 5 |
| `ip_zones` | 0 | 2 | 1 |
| `ip_zones_detail` | 0 | 9 | 1 |
| `isg_profiles` | 2 | 10 | 1 |
| `license_data` | 0 | 6 | 1 |
| `messages` | 304 | 12 | 3 |
| `messages_status` | 300 | 4 | 2 |
| `nas_info` | 6 | 10 | 1 |
| `nas_settings` | 0 | 4 | 1 |
| `netflow_providers` | 0 | 8 | 1 |
| `once_service_data` | 43 | 4 | 1 |
| `once_service_links` | 2865 | 4 | 1 |
| `payments_timed` | 30 | 9 | 1 |
| `payment_methods` | 7 | 2 | 1 |
| `payment_orders` | 0 | 12 | 1 |
| `payment_transactions` | 264829 | 21 | 4 |
| `periodic_services_data` | 228 | 9 | 1 |
| `periodic_service_links` | 18150 | 24 | 1 |
| `promised_payment_data` | 859 | 3 | 1 |
| `promised_payment_settings` | 0 | 13 | 1 |
| `radius_accounts` | 101 | 5 | 1 |
| `radius_custom_data` | 616 | 4 | 1 |
| `radius_data` | 823 | 10 | 2 |
| `radius_pool_info` | 0 | 10 | 1 |
| `routers_info` | 162 | 10 | 1 |
| `services_data` | 271 | 12 | 2 |
| `service_links` | 21063 | 6 | 2 |
| `slink_tech_param` | 0 | 6 | 1 |
| `slink_tech_param_type` | 2 | 3 | 1 |
| `streets` | 0 | 5 | 1 |
| `supplier_charges` | 0 | 11 | 1 |
| `supplier_info` | 0 | 23 | 1 |
| `supplier_payments` | 0 | 7 | 1 |
| `switches` | 426 | 12 | 1 |
| `switches_dhcp_pool_links` | 0 | 4 | 1 |
| `switch_tariff_data` | 0 | 8 | 1 |
| `switch_tariff_settings` | 0 | 6 | 1 |
| `switch_types` | 20 | 17 | 1 |
| `system_accounts` | 103 | 12 | 1 |
| `system_groups` | 85 | 3 | 1 |
| `system_group_info` | 4 | 3 | 1 |
| `system_rights` | 219 | 2 | 1 |
| `tariffs` | 212 | 10 | 1 |
| `tariffs_history` | 15151 | 6 | 1 |
| `tariffs_services_link` | 213 | 4 | 1 |
| `tel_consumption_info` | 0 | 4 | 2 |
| `tel_directions` | 0 | 5 | 1 |
| `tel_directions_v2` | 0 | 15 | 1 |
| `tel_emergency_calls` | 0 | 4 | 1 |
| `tel_numbers` | 0 | 11 | 1 |
| `tel_services_borders` | 0 | 6 | 1 |
| `tel_services_data` | 0 | 14 | 1 |
| `tel_services_fixed_cost` | 0 | 5 | 1 |
| `tel_services_prepaid` | 0 | 5 | 1 |
| `tel_services_tr_mult` | 0 | 6 | 1 |
| `tel_service_links` | 0 | 2 | 1 |
| `tel_service_links_downloaded` | 0 | 5 | 1 |
| `tel_service_links_prepaid` | 0 | 7 | 1 |
| `tel_sessions_detail` | 0 | 12 | 2 |
| `tel_sessions_log` | 0 | 45 | 5 |
| `tel_sessions_log_attrs` | 0 | 7 | 1 |
| `tel_zones` | 0 | 3 | 1 |
| `tel_zones_v2` | 0 | 6 | 1 |
| `template_type` | 31 | 4 | 1 |
| `temp_ip` | 0 | 4 | 1 |
| `time_range` | 27 | 4 | 1 |
| `time_range_days` | 0 | 5 | 1 |
| `time_range_detail` | 183 | 11 | 1 |
| `traffic_collectors` | 0 | 4 | 1 |
| `traffic_consumption` | 0 | 4 | 2 |
| `traf_serv_tclasses` | 90 | 8 | 1 |
| `turbo_mode_links` | 0 | 7 | 1 |
| `turbo_mode_settings` | 0 | 11 | 1 |
| `t_class` | 4 | 9 | 1 |
| `t_class_detail` | 25 | 38 | 1 |
| `uaddparams_desc` | 6 | 4 | 0 |
| `users` | 5005 | 44 | 1 |
| `users_accounts` | 5099 | 4 | 2 |
| `users_groups_link` | 693 | 3 | 2 |
| `user_additional_params` | 876 | 4 | 1 |
| `user_contacts` | 11728 | 11 | 1 |
| `user_contact_em` | 3 | 2 | 1 |
| `user_log` | 333396 | 7 | 1 |
| `utm5_cashier_settings` | 22 | 3 | 1 |
| `utm5_settings` | 27 | 7 | 2 |
| `utm5_tray_settings` | 16 | 4 | 1 |
| `vod_services_data` | 0 | 3 | 0 |
| `voluntary_suspension_data` | 0 | 5 | 1 |
| `voluntary_suspension_settings` | 0 | 14 | 1 |

## Область Subscriber 360

Проверенные таблицы-кандидаты: `users`, `accounts`, `users_accounts`,
`service_links`, `services_data`, `periodic_service_links`,
`iptraffic_service_links`, `ip_groups`, `switches`, `switch_types`,
`account_tariff_link`, `tariffs`, `blocks_info`. JOIN и смысл описаны в
других отчётах; совпадение имён колонок само по себе не доказывает связь.

Большие журналы сессий/трафика/истории (`dhs_sessions_log`,
`discount_transactions_all`, `balance_history`, `dhcp_leases_log`)
инвентаризованы только по метаданным. Полного сканирования этих таблиц не было.
