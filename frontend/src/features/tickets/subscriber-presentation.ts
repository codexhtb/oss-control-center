import type { SubscriberConnection, SubscriberIpRow } from './types'

export function formatSubscriberAddress(address?: string | null, house?: string | null): string | null {
  const base = address?.trim()
  const number = house?.trim()
  if (!base) return number ? `д. ${number}` : null
  if (!number) return base
  const escaped = number.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const explicitHouse = new RegExp(`(?:^|[,;\\s])(?:д(?:ом)?\\.?|строение|стр\\.?)\\s*${escaped}(?:$|[,;\\s])`, 'iu')
  const trailingNumber = new RegExp(`(?:^|[,;\\s])${escaped}\\s*$`, 'u')
  return explicitHouse.test(base) || trailingNumber.test(base) ? base : `${base.replace(/[,.\\s]+$/u, '')}, д. ${number}`
}

export function configuredIpv4(rows: SubscriberIpRow[]): string[] {
  return [...new Set(rows.flatMap((row) => row.configured_ipv4 && row.configured_prefix_length !== null && row.configured_prefix_length !== undefined
    ? [`${row.configured_ipv4}/${row.configured_prefix_length}`]
    : []))]
}

export function connectionConfiguredIpv4(connection?: SubscriberConnection | null): string[] {
  return configuredIpv4(connection?.ip_groups.flatMap((group) => group.rows) ?? [])
}
