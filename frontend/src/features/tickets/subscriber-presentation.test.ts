import { describe, expect, it } from 'vitest'
import { configuredIpv4, formatSubscriberAddress } from './subscriber-presentation'
import type { SubscriberIpRow } from './types'

describe('subscriber presentation', () => {
  it('adds the NetUP building as a house number without duplicating it', () => {
    expect(formatSubscriberAddress('г. Сухум, ул. Ладария', '20')).toBe('г. Сухум, ул. Ладария, д. 20')
    expect(formatSubscriberAddress('г. Сухум, ул. Ладария, д. 20', '20')).toBe('г. Сухум, ул. Ладария, д. 20')
    expect(formatSubscriberAddress('г. Сухум, ул. Ладария 20', '20')).toBe('г. Сухум, ул. Ладария 20')
  })

  it('shows every configured address once and ignores pool-only rows', () => {
    const row = (address: string | null, prefix: number | null): SubscriberIpRow => ({
      row_id: crypto.randomUUID(), ip_type_code: 4, configured_ipv4: address,
      configured_prefix_length: prefix, address_assignment: address ? 'CONFIGURED' : 'AUTO_POOL',
      ip_raw: null, mask_raw: null, ip_ext_raw: null, mask_ext_raw: null,
      dhcp_pool_id: address ? null : '7', switch_id: null, device: null,
      port_id: null, vlan_id: null, field_meta: {},
    })
    expect(configuredIpv4([row('100.64.2.102', 32), row(null, null), row('100.64.2.102', 32)])).toEqual(['100.64.2.102/32'])
  })
})
