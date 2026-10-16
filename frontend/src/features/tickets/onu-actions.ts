import type { OnuCandidate, OnuResult } from './types'

export function confirmedOnu(result: OnuResult): OnuCandidate | null {
  return result.availability === 'AVAILABLE' && result.association === 'MATCHED_BY_RULE'
    && result.match && result.candidates.length === 1 ? result.candidates[0]! : null
}

export function sourceTime(value: string | null): string {
  if (!value) return 'Время неизвестно'
  return value.replace(/^(\d{4})-(\d{2})-(\d{2})[ T]/, '$3.$2.$1 ')
}

export function diagnosticSummary(item: OnuCandidate, vlan: number): string {
  const lines = [
    item.olt ? `OLT: ${item.olt}` : null,
    `ONU: ${item.interface}`,
    `VLAN: ${vlan}`,
    item.mac ? `MAC: ${item.mac}` : null,
    item.serial ? `Serial: ${item.serial}` : null,
    `Состояние: ${item.state === 'UNKNOWN' ? 'Неизвестно' : item.state === 'ONLINE' ? 'Online' : item.state === 'OFFLINE' ? 'Offline' : 'Deregistered'}`,
    (item.state === 'OFFLINE' || item.state === 'DEREGISTERED') && item.reason && item.reason !== 'unknown'
      ? `Причина отключения: ${item.reason}` : null,
    (item.state === 'OFFLINE' || item.state === 'DEREGISTERED') && item.disconnection_at
      ? `${item.disconnection_time_basis === 'EPON_DEREGISTRATION' ? 'Время отключения' : 'Отключение обнаружено'}: ${sourceTime(item.disconnection_at)}` : null,
    `ONU Rx: ${item.onu_rx_dbm === null ? 'Нет измерения' : `${item.onu_rx_dbm} dBm`}`,
    `OLT Rx: ${item.olt_rx_dbm === null ? 'Нет измерения' : `${item.olt_rx_dbm} dBm`}`,
    `ONU Tx: ${item.onu_tx_dbm === null ? 'Нет измерения' : `${item.onu_tx_dbm} dBm`}`,
    `Последний опрос: ${sourceTime(item.inventory_polled_at)}`,
    `Оптика измерена: ${sourceTime(item.optical_measured_at)}`,
  ]
  return lines.filter((line): line is string => line !== null).join('\n')
}
