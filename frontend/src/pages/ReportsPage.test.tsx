import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import { getCallbacksReport, getEmployeesReport, getFieldWorkReport, getGeneralReport, getReasonsReport, getReportFile } from '../features/reports/report-api'
import { ReportsPage, periodFor, periodForMonth } from './ReportsPage'

vi.mock('../features/reports/report-api', () => ({ getGeneralReport: vi.fn(), getEmployeesReport: vi.fn(), getCallbacksReport: vi.fn(), getFieldWorkReport: vi.fn(), getReasonsReport: vi.fn(), getReportFile: vi.fn() }))

describe('Service Desk reports', () => {
  it('uses calendar boundaries in UTC+3, including month changes', () => {
    const now = new Date('2026-10-31T22:15:00Z')
    expect(periodFor('today', now)).toEqual({ start: '2026-11-01', end: '2026-11-01' })
    expect(periodFor('yesterday', now)).toEqual({ start: '2026-10-31', end: '2026-10-31' })
    expect(periodFor('previous', now)).toEqual({ start: '2026-10-01', end: '2026-10-31' })
    expect(periodForMonth('2024-02')).toEqual({ start: '2024-02-01', end: '2024-02-29' })
    expect(periodForMonth('2026-13')).toBeNull()
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

  it('shows employee actions and callback events with ticket links', async () => {
    vi.mocked(getEmployeesReport).mockResolvedValue({
      start: '2026-11-11', end: '2026-11-11',
      items: [{ user_id: 'user-1', display_name: 'Тестовый инженер', role: 'engineer', is_active: true,
        created_tickets: 1, decision_events: 2, callback_events: 1, callback_tickets: 1,
        outcomes: { CONFIRMED: 1, STILL_BROKEN: 0, NO_ANSWER: 0 } }],
      daily: [{ user_id: 'user-1', date: '2026-11-11', created: 1, decisions: 2, callbacks: 1 }],
    })
    vi.mocked(getCallbacksReport).mockResolvedValue({
      start: '2026-11-11', end: '2026-11-11', total_events: 2, unique_tickets: 1,
      outcomes: { CONFIRMED: 1, STILL_BROKEN: 0, NO_ANSWER: 1 },
      daily: [{ date: '2026-11-11', outcome: 'CONFIRMED', count: 1 }],
      employees: [{ user_id: 'user-1', display_name: 'Тестовый инженер', outcome: 'CONFIRMED', count: 1 }],
      details: { page: 1, size: 20, total: 2, items: [{
        id: 'callback-1', ticket_id: 'ticket-1', ticket_number: 77, subject: 'Тестовая тема',
        outcome: 'CONFIRMED', occurred_at: '2026-11-11T10:00:00Z', actor_id: 'user-1', actor_name: 'Тестовый инженер',
      }] },
    })
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ReportsPage /></MemoryRouter></QueryClientProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Сотрудники' }))
    expect(await screen.findByRole('cell', { name: 'Тестовый инженер' })).toBeInTheDocument()
    expect(screen.getByText('Уникальные заявки со звонками')).toBeInTheDocument()
    expect(screen.getByText('2026-11-11')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Контрольные звонки' }))
    expect(await screen.findByRole('link', { name: '#77' })).toHaveAttribute('href', '/tickets/ticket-1')
    expect(screen.getByText('Уникальные заявки')).toBeInTheDocument()
    await waitFor(() => expect(vi.mocked(getCallbacksReport)).toHaveBeenCalled())
  })

  it('shows field dispatch event counts and direction filter', async () => {
    vi.mocked(getFieldWorkReport).mockResolvedValue({
      start: '2026-11-11', end: '2026-11-11',
      metrics: { sent_handoffs: 1, sent_unique_tickets: 1, reported_handoffs: 0, verified_handoffs: 0, reschedule_events: 2, rescheduled_handoffs: 1, awaiting_report_now: 1, date_pending_now: 1 },
      directions: { SUKHUM: { sent: 1, reported: 0, verified: 0, reschedules: 2 } },
      daily: [{ date: '2026-11-11', sent: 1, reported: 0, verified: 0, reschedules: 2 }],
      reschedule_reasons: [{ reason: 'Попросил позже', count: 2 }],
      details: { page: 1, size: 20, total: 1, items: [{ id: 'handoff-1', ticket_id: 'ticket-1', ticket_number: 88, subject: 'Выезд', direction: 'SUKHUM', work_status: 'AWAITING_REPORT', schedule_status: 'DATE_PENDING', sent_at: '2026-11-11T10:00:00Z', reported_at: null, verified_at: null }] },
    })
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ReportsPage /></MemoryRouter></QueryClientProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Монтажные работы' }))
    expect(await screen.findByRole('link', { name: '#88' })).toHaveAttribute('href', '/tickets/ticket-1')
    expect(screen.getByText('Отправленное задание не считается выполненным.', { exact: false })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Направление'), { target: { value: 'OCHAMCHIRA' } })
    await waitFor(() => expect(getFieldWorkReport).toHaveBeenCalledWith(expect.any(String), expect.any(String), 'OCHAMCHIRA', 1, expect.anything()))
  })

  it('separates registration categories from decision reasons', async () => {
    vi.mocked(getReasonsReport).mockResolvedValue({
      start: '2026-11-11', end: '2026-11-11',
      metrics: { created_tickets: 1, without_category: 0, decision_events: 2, decision_unique_tickets: 1, decisions_without_reason: 0, created_without_reason_now: 0 },
      categories: [{ name: 'Нет связи', count: 1 }], reasons: [{ name: 'Обрыв линии', count: 2 }],
      daily: [{ date: '2026-11-11', category: 'Нет связи', count: 1 }],
      details: { page: 1, size: 20, total: 1, items: [{ id: 'ticket-1', ticket_id: 'ticket-1', ticket_number: 89, subject: 'Тема', name: 'Нет связи', occurred_at: '2026-11-11T10:00:00Z' }] },
    })
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ReportsPage /></MemoryRouter></QueryClientProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Причины обращений' }))
    expect(await screen.findByRole('link', { name: '#89' })).toHaveAttribute('href', '/tickets/ticket-1')
    expect(screen.getByText('Обрыв линии')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('События'), { target: { value: 'decision' } })
    await waitFor(() => expect(getReasonsReport).toHaveBeenCalledWith(expect.any(String), expect.any(String), '', 'decision', 1, expect.anything()))
  })

  it('exports the selected UTC+3 month as a unified PDF', async () => {
    vi.mocked(getReportFile).mockResolvedValue(new Blob(['pdf'], { type: 'application/pdf' }))
    const OriginalURL = URL
    vi.stubGlobal('URL', class extends OriginalURL {
      static createObjectURL = vi.fn(() => 'blob:report')
      static revokeObjectURL = vi.fn()
    })
    render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ReportsPage /></MemoryRouter></QueryClientProvider>)
    fireEvent.change(screen.getByLabelText('Отчёт за месяц'), { target: { value: '2024-02' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сформировать PDF' }))
    await waitFor(() => expect(getReportFile).toHaveBeenCalledWith('pdf', '2024-02-01', '2024-02-29', '', ''))
    vi.unstubAllGlobals()
  })
})
