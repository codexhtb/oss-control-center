import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { OnuCard } from './OnuCard'

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
  expect(screen.getByText(/Кандидаты по OLT и VLAN/)).toBeInTheDocument()
  expect(screen.getByText(/Последний опрос ONU: 2026-10-10 12:00:00/)).toBeInTheDocument()
  expect(screen.getByText(/Оптика: 2026-10-09 04:10:00/)).toBeInTheDocument()
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
