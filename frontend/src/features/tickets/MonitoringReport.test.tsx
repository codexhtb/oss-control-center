import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { MonitoringReport } from './MonitoringReport'
import type { DiagnosticReport } from './types'

const report: DiagnosticReport = {
  connection_snapshot_id: 'synthetic-connection', checked_at: '2026-10-10T12:00:00Z',
  device_match: 'KNOWN', interface_match: 'UNKNOWN', limitations: ['interface_mapping_unconfirmed'],
  observations: [{ source: 'LIBRENMS', measured_object: 'device:1', result: 'UP',
    observed_at: '2026-10-10T11:59:00Z', retrieved_at: '2026-10-10T12:00:00Z', quality: 'KNOWN' }],
}

it('preserves the existing Ethernet report and its interface semantics', () => {
  render(<MonitoringReport report={report} />)
  expect(screen.getByText('device:1')).toBeVisible()
  expect(screen.getByText(/интерфейс: Неизвестно/)).toBeVisible()
  expect(screen.getByText('Соответствие порта NetUP и интерфейса не подтверждено.')).toBeVisible()
  expect(screen.queryByText('Устройство OLT')).not.toBeInTheDocument()
})

it('keeps unavailable observations distinct from DOWN and does not invent an interface', () => {
  render(<MonitoringReport report={{ ...report, observations: [{ ...report.observations[0]!,
    result: 'UNAVAILABLE', quality: 'UNAVAILABLE', observed_at: null,
  }] }} />)
  expect(screen.getAllByText('Источник недоступен')[0]).toBeVisible()
  expect(screen.getAllByText('Время неизвестно')[0]).toBeVisible()
  expect(screen.queryByText('Недоступно')).not.toBeInTheDocument()
  expect(screen.queryByText('Интерфейс оборудования')).not.toBeInTheDocument()
})
