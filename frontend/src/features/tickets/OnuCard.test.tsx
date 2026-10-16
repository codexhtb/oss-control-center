import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { OnuCard } from './OnuCard'
import type { OnuResult } from './types'
import { diagnosticSummary } from './onu-actions'

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
  expect(screen.queryByText('Наблюдение состояния ONU')).not.toBeInTheDocument()
  expect(screen.getByText('Оптика измерена').parentElement).toHaveTextContent('09.10.2026 04:10:00')
  expect(screen.queryByText('Источники и актуальность')).not.toBeInTheDocument()
  expect(screen.queryByText('Старое измерение')).not.toBeInTheDocument()
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
  expect(screen.queryByText(/порт 14 → PON 1, VLAN 101/)).not.toBeInTheDocument()
  expect(screen.getByText('SYNTHETIC-SERIAL')).toBeInTheDocument()
  expect(screen.getByText('Online')).toBeInTheDocument()
  expect(screen.getByRole('group', { name: 'ONU Rx' })).toHaveTextContent('-20.5 dBm')
  expect(screen.getByText('VLAN подключения · NetUP').parentElement).toHaveTextContent('101')
  expect(screen.getByText('101')).toBeVisible()
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

it('labels EPON event time and GPON initial detection differently, and hides old online time', () => {
  const epon: OnuResult = { ...confirmed, candidates: [{ ...confirmed.candidates[0]!,
    state: 'DEREGISTERED', disconnection_at: '2026-10-10 10:00:00',
    disconnection_time_basis: 'EPON_DEREGISTRATION',
  }] }
  const view = render(<OnuCard result={epon} />)
  expect(screen.getByText('Время отключения:')).toHaveTextContent('Время отключения:')
  expect(screen.getByText('Время отключения:').parentElement).toHaveTextContent('10.10.2026 10:00:00')
  expect(diagnosticSummary(epon.candidates[0]!, 101)).toContain('Время отключения: 10.10.2026 10:00:00')
  view.rerender(<OnuCard result={{ ...epon, candidates: [{ ...epon.candidates[0]!, pon_type: 'GPON',
    state: 'OFFLINE', mac: null, serial: 'SYNTHETIC-SERIAL', disconnection_time_basis: 'FIRST_OBSERVED_OFFLINE',
  }] }} />)
  expect(screen.getByText('Отключение обнаружено:')).toBeVisible()
  expect(screen.queryByText('Время отключения:')).not.toBeInTheDocument()
  view.rerender(<OnuCard result={{ ...epon, candidates: [{ ...epon.candidates[0]!, state: 'ONLINE' }] }} />)
  expect(screen.queryByText('Время отключения:')).not.toBeInTheDocument()
  expect(screen.queryByText('Отключение обнаружено:')).not.toBeInTheDocument()
})

it('does not present a disconnected source as a working ONU', () => {
  render(<OnuCard result={{
    source: 'BDCOM', retrieved_at: '2026-10-10T12:00:00Z',
    availability: 'UNAVAILABLE', association: 'UNCONFIRMED', candidates: [],
    limitations: ['onu_source_not_configured', 'netup_topology_stale'], time_basis: 'SOURCE_LOCAL_UNVERIFIED',
  }} />)
  expect(screen.getByText(/Источник BDCOM ONU не подключён/)).toBeInTheDocument()
  expect(screen.queryByText('Online')).not.toBeInTheDocument()
  expect(screen.queryByText('NetUP: историческая лабораторная копия.')).not.toBeInTheDocument()
})

const sample: OnuResult = {
  source: 'BDCOM', retrieved_at: '2026-10-10T12:00:00Z', availability: 'AVAILABLE',
  association: 'UNCONFIRMED', time_basis: 'SOURCE_LOCAL_UNVERIFIED',
  limitations: ['netup_topology_stale'], candidates: [{
    olt: 'synthetic-olt', interface: 'EPON0/1:17', pon_type: 'EPON',
    mac: '02:00:00:00:00:01', serial: null, state: 'ONLINE', reason: null,
    onu_rx_dbm: '-20', olt_rx_dbm: '-24', onu_tx_dbm: '2.5',
    inventory_polled_at: '2026-10-10 11:30:00', optical_measured_at: '2026-10-01 04:10:00',
  }],
}

it('keeps Rx values together, Tx third, and reports old optical dates without changing ONU state', () => {
  render(<OnuCard result={sample} vlan="101" />)
  expect(screen.getAllByRole('group', { name: /^(ONU Rx|OLT Rx|ONU Tx)$/ }).map((node) => node.getAttribute('aria-label'))).toEqual(['ONU Rx', 'OLT Rx', 'ONU Tx'])
  expect(screen.getByRole('group', { name: 'ONU Tx' })).toHaveTextContent('2.5 dBm')
  expect(screen.getByText('Online')).toBeVisible()
  expect(screen.getByText('По опросу ONU')).toBeVisible()
  expect(screen.getByText('Старое измерение')).toBeVisible()
  expect(screen.getByText('Старое измерение').parentElement).toHaveTextContent('Оптика измерена')
  expect(screen.queryByText('NetUP: историческая лабораторная копия.')).not.toBeInTheDocument()
  expect(screen.getByText('101')).toBeVisible()
  expect(screen.getAllByText(/Привязка к абоненту не подтверждена/)[0]).toBeVisible()
})

it('shows missing optical measurements and times explicitly without inventing a value or freshness', () => {
  render(<OnuCard result={{ ...sample, candidates: [{ ...sample.candidates[0]!,
    state: 'UNKNOWN', onu_rx_dbm: null, olt_rx_dbm: null, onu_tx_dbm: null,
    inventory_polled_at: null, optical_measured_at: null,
  }] }} />)
  expect(screen.getAllByText('Нет измерения')).toHaveLength(3)
  expect(screen.getAllByText('Время неизвестно')).toHaveLength(1)
  expect(screen.queryByText('dBm')).not.toBeInTheDocument()
  expect(screen.queryByText('Старое измерение')).not.toBeInTheDocument()
  expect(screen.queryByText('Online')).not.toBeInTheDocument()
})

const confirmed: OnuResult = {
  ...sample, association: 'MATCHED_BY_RULE',
  match: { netup_port: 7, pon_port: 1, vlan: 101, olt_mapping_basis: 'manual_verified',
    billing_observed_at: null, billing_source: 'NETUP', onu_source: 'LIBRENMS_BDCOM' },
}

it('copies exact confirmed EPON fields and a summary without a stale online reason', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  render(<OnuCard result={{ ...confirmed, candidates: [{ ...confirmed.candidates[0]!, reason: 'power-off' }] }} onRefresh={async () => confirmed} />)
  for (const [label, value] of [
    ['OLT', 'synthetic-olt'], ['интерфейс ONU', 'EPON0/1:17'],
    ['MAC ONU', '02:00:00:00:00:01'], ['VLAN', '101'],
  ]) {
    fireEvent.click(screen.getByRole('button', { name: `Скопировать ${label}` }))
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(value))
  }
  expect(screen.queryByRole('button', { name: 'Скопировать Serial ONU' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Скопировать сводку' }))
  await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(diagnosticSummary(confirmed.candidates[0]!, 101)))
  const summary = vi.mocked(writeText).mock.lastCall![0] as string
  expect(summary).toContain('MAC: 02:00:00:00:00:01')
  expect(summary).toContain('ONU Rx: -20 dBm')
  expect(summary).toContain('Оптика измерена: 01.10.2026 04:10:00')
  expect(summary).not.toContain('power-off')
  expect(summary).not.toContain('IP:')
  expect(summary).not.toContain('NetUP: историческая лабораторная копия')
  expect(screen.getByRole('status')).toHaveTextContent('Скопировано')
})

it('copies GPON Serial, omits absent MAC and includes only current offline reason', async () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  const gpon: OnuResult = { ...confirmed, candidates: [{ ...confirmed.candidates[0]!,
    pon_type: 'GPON', interface: 'GPON0/3:7', mac: null, serial: 'SYNTHETIC-SERIAL',
    state: 'OFFLINE', reason: 'power-off', onu_tx_dbm: null, optical_measured_at: null,
  }] }
  render(<OnuCard result={gpon} onRefresh={async () => confirmed} />)
  expect(screen.queryByRole('button', { name: 'Скопировать MAC ONU' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Скопировать Serial ONU' }))
  await waitFor(() => expect(writeText).toHaveBeenLastCalledWith('SYNTHETIC-SERIAL'))
  fireEvent.click(screen.getByRole('button', { name: 'Скопировать сводку' }))
  await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2))
  const summary = vi.mocked(writeText).mock.lastCall![0] as string
  expect(summary).toContain('Serial: SYNTHETIC-SERIAL')
  expect(summary).toContain('Причина отключения: power-off')
  expect(summary).toContain('ONU Tx: Нет измерения')
  expect(summary).toContain('Оптика измерена: Время неизвестно')
  expect(summary).not.toContain('MAC:')
})

it('does not offer copying for ambiguous or unavailable ONU data', () => {
  const { rerender } = render(<OnuCard result={sample} onRefresh={async () => confirmed} />)
  expect(screen.queryByRole('button', { name: /Скопировать/ })).not.toBeInTheDocument()
  rerender(<OnuCard result={{ ...confirmed, availability: 'UNAVAILABLE' }} onRefresh={async () => confirmed} />)
  expect(screen.queryByRole('button', { name: /Скопировать/ })).not.toBeInTheDocument()
  rerender(<OnuCard result={{ ...confirmed, candidates: [confirmed.candidates[0]!, confirmed.candidates[0]!] }} onRefresh={async () => confirmed} />)
  expect(screen.queryByRole('button', { name: /Скопировать/ })).not.toBeInTheDocument()
})

it('refreshes once per click, reports success or source error, and preserves source timestamps', async () => {
  let resolveRefresh!: () => void
  const onRefresh = vi.fn(() => new Promise<OnuResult>((resolve) => { resolveRefresh = () => resolve(confirmed) }))
  const { rerender } = render(<OnuCard result={confirmed} onRefresh={onRefresh} />)
  fireEvent.click(screen.getByRole('button', { name: 'Обновить данные' }))
  expect(screen.getByRole('button', { name: 'Обновляем…' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Обновляем…' }))
  expect(onRefresh).toHaveBeenCalledTimes(1)
  resolveRefresh()
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Данные обновлены'))
  expect(screen.getByText('Оптика измерена').parentElement).toHaveTextContent('01.10.2026 04:10:00')
  rerender(<OnuCard result={confirmed} onRefresh={async () => { throw new Error('synthetic failure') }} />)
  fireEvent.click(screen.getByRole('button', { name: 'Обновить данные' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Источник недоступен'))
  rerender(<OnuCard result={confirmed} onRefresh={async () => ({ ...confirmed, availability: 'UNAVAILABLE' })} />)
  fireEvent.click(screen.getByRole('button', { name: 'Обновить данные' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Источник недоступен'))
})
