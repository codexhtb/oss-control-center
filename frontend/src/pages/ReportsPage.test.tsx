import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import { getGeneralReport } from '../features/reports/report-api'
import { ReportsPage, periodFor } from './ReportsPage'

vi.mock('../features/reports/report-api', () => ({ getGeneralReport: vi.fn() }))

describe('Service Desk reports', () => {
  it('uses calendar boundaries in UTC+3, including month changes', () => {
    const now = new Date('2026-10-31T22:15:00Z')
    expect(periodFor('today', now)).toEqual({ start: '2026-11-01', end: '2026-11-01' })
    expect(periodFor('yesterday', now)).toEqual({ start: '2026-10-31', end: '2026-10-31' })
    expect(periodFor('previous', now)).toEqual({ start: '2026-10-01', end: '2026-10-31' })
  })

  it('shows real metrics and links to the original ticket', async () => {
    vi.mocked(getGeneralReport).mockResolvedValue({
      start: '2026-11-01', end: '2026-11-01',
      metrics: { created: 1, resolved_events: 2, closed_events: 1, returned_events: 1,
        active_now: 3, average_resolution_seconds: 3600, average_closure_seconds: null },
      daily: [{ date: '2026-11-01', created: 1, closed: 1 }],
      distribution: { types: { MANUAL: 1 }, statuses: { CLOSED: 1 }, priorities: { NORMAL: 1 } },
      details: { page: 1, size: 20, total: 1, items: [{
        id: '00000000-0000-4000-8000-000000000001', number: 42, subject: 'Тестовая тема',
        ticket_type: 'MANUAL', status: 'CLOSED', priority: 'NORMAL',
        assignee_name: null, created_at: '2026-11-01T12:00:00Z',
      }] },
    })
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ReportsPage /></MemoryRouter></QueryClientProvider>)
    expect(await screen.findByRole('link', { name: '#42' })).toHaveAttribute('href', '/tickets/00000000-0000-4000-8000-000000000001')
    expect(screen.getByText('Тестовая тема')).toBeInTheDocument()
    expect(screen.getByText('Активны сейчас')).toBeInTheDocument()
    expect(screen.getByText('Нет событий')).toBeInTheDocument()
  })
})
