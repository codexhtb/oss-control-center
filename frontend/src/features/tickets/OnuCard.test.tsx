import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { OnuCard } from './OnuCard'
import type { OnuResult } from './types'

it('shows GPON Serial without fabricating a MAC and keeps source times separate', () => {
  render(<OnuCard result={{
    source: 'BDCOM', retrieved_at: '2026-10-10T12:00:00Z',
    availability: 'AVAILABLE', association: 'UNCONFIRMED', time_basis: 'SOURCE_LOCAL_UNVERIFIED',
    limitations: ['onu_association_unconfirmed'], candidates: [{
      olt: 'synthetic-olt', interface: 'GPON0/1:2', pon_type: 'GPON', mac: null,
      serial: 'SYNTHETIC-SERIAL', state: 'ONLINE', reason: null,
      onu_rx_dbm: '-20.5', olt_rx_dbm: '-24', onu_tx_dbm: null,
      inventory_polled_at: '2026-10-10 12:00:00', optical_measured_at: '2026-10-09 04:10:00',
    }],
  }} />)
  expect(screen.getByText('SYNTHETIC-SERIAL')).toBeInTheDocument()
  expect(screen.getByText('Отсутствует')).toBeInTheDocument()
  expect(screen.getByText(/Кандидаты для проверки инженером/)).toBeInTheDocument()
  expect(screen.getByText(/Опрос: 2026-10-10 12:00:00/)).toBeInTheDocument()
  expect(screen.getByText(/Оптика: 2026-10-09 04:10:00/)).toBeInTheDocument()
})

it('shows a confirmed composite-key match with NetUP provenance', () => {
  render(<OnuCard result={{
    source: 'BDCOM', retrieved_at: '2026-10-10T12:00:00Z',
    availability: 'AVAILABLE', association: 'MATCHED_BY_RULE', time_basis: 'SOURCE_LOCAL_UNVERIFIED',
    match: { netup_port: 14, pon_port: 1, vlan: 101, olt_mapping_basis: 'manual_verified',
      billing_observed_at: '2026-10-10T12:00:00Z', billing_source: 'NETUP', onu_source: 'LIBRENMS_BDCOM' },
    limitations: [], candidates: [{
      olt: 'synthetic-olt', interface: 'GPON0/1:2', pon_type: 'GPON', mac: null,
      serial: 'SYNTHETIC-SERIAL', state: 'ONLINE', reason: null,
      onu_rx_dbm: '-20.5', olt_rx_dbm: '-24', onu_tx_dbm: null,
      inventory_polled_at: '2026-10-10 12:00:00', optical_measured_at: '2026-10-09 04:10:00',
    }],
  }} />)
  expect(screen.getByText(/порт 14 → PON 1, VLAN 101/)).toBeInTheDocument()
  expect(screen.getByText('SYNTHETIC-SERIAL')).toBeInTheDocument()
  expect(screen.getByText('Online')).toBeInTheDocument()
  expect(screen.getByText('-20.5 dBm')).toBeInTheDocument()
})

it('does not show an old power-off reason for an online ONU, but shows it when offline', () => {
  const result: OnuResult = {
    source: 'BDCOM' as const, retrieved_at: '2026-10-10T12:00:00Z',
    availability: 'AVAILABLE' as const, association: 'MATCHED_BY_RULE' as const,
    time_basis: 'SOURCE_LOCAL_UNVERIFIED' as const, limitations: [],
    candidates: [{ olt: 'synthetic-olt', interface: 'EPON0/1:17', pon_type: 'EPON' as const,
      mac: '02:00:00:00:00:01', serial: null, state: 'ONLINE' as const, reason: 'power-off',
      onu_rx_dbm: '-20', olt_rx_dbm: '-24', onu_tx_dbm: null,
      inventory_polled_at: '2026-10-10 12:00:00', optical_measured_at: '2026-10-09 04:10:00' }],
  }
  const view = render(<OnuCard result={result} />)
  expect(screen.getByText('Online')).toBeInTheDocument()
  expect(screen.queryByText('power-off')).not.toBeInTheDocument()
  view.rerender(<OnuCard result={{ ...result, candidates: [{ ...result.candidates[0]!, state: 'OFFLINE' }] }} />)
  expect(screen.getByText('Offline')).toBeInTheDocument()
  expect(screen.getByText('power-off')).toBeInTheDocument()
  view.rerender(<OnuCard result={{ ...result, candidates: [{ ...result.candidates[0]!, state: 'DEREGISTERED' }] }} />)
  expect(screen.getByText('Deregistered')).toBeInTheDocument()
  expect(screen.getByText('power-off')).toBeInTheDocument()
})

it('does not present a disconnected source as a working ONU', () => {
  render(<OnuCard result={{
    source: 'BDCOM', retrieved_at: '2026-10-10T12:00:00Z',
    availability: 'UNAVAILABLE', association: 'UNCONFIRMED', candidates: [],
    limitations: ['onu_source_not_configured'], time_basis: 'SOURCE_LOCAL_UNVERIFIED',
  }} />)
  expect(screen.getByText(/Источник BDCOM ONU не подключён/)).toBeInTheDocument()
  expect(screen.queryByText('Online')).not.toBeInTheDocument()
})
