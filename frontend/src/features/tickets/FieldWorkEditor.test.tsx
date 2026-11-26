import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { FieldWorkEditor } from './FieldWorkEditor'
import { ticketApi } from './ticket-api'
import type { FieldWorkHandoff } from './types'

vi.mock('./ticket-api', () => ({ ticketApi: { updateFieldDetails: vi.fn(), fieldWorkChanges: vi.fn() } }))
const handoff = {
  id: 'handoff', reason: 'Начальная причина', note: 'MAC: AA:BB', visit_address: null,
  details_version: 4, delivery_status: 'PUBLISHED', work_status: 'AWAITING_REPORT',
} as FieldWorkHandoff
function mount(value = handoff) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><FieldWorkEditor ticketId="ticket" handoff={value} address="Исходный адрес" /></QueryClientProvider>)
}
beforeEach(() => {
  vi.mocked(ticketApi.updateFieldDetails).mockReset().mockResolvedValue(handoff)
  vi.mocked(ticketApi.fieldWorkChanges).mockReset().mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0 })
})
it('updates the existing handoff with pasted multiline text and a version without another dispatch', async () => {
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Изменить / дополнить задание' }))
  const reason = 'Ремонтные работы. Свяжитесь, пожалуйста.\nMAC < > & «текст»'
  fireEvent.change(screen.getByRole('textbox', { name: 'Причина выезда' }), { target: { value: reason } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Примечание к заданию' }), { target: { value: 'я'.repeat(10000) } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Дополнительные сведения' }), { target: { value: 'Позвонить перед выездом' } })
  expect(screen.getByRole('textbox', { name: 'Причина выезда' })).toHaveAttribute('maxlength', '10000')
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить задание' }))
  await waitFor(() => expect(ticketApi.updateFieldDetails).toHaveBeenCalledWith('ticket', 'handoff', {
    reason, note: 'я'.repeat(10000), visit_address: 'Исходный адрес',
    additional_information: 'Позвонить перед выездом', expected_version: 4, client_request_id: expect.any(String),
  }))
  expect(await screen.findByText('Изменений пока нет.')).toBeInTheDocument()
})
it('keeps the draft and explains a conflict, and completed work has no edit action', async () => {
  const { ApiError } = await import('../../shared/api/client')
  vi.mocked(ticketApi.updateFieldDetails).mockRejectedValue(new ApiError(409, 'version_conflict'))
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Изменить / дополнить задание' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Дополнительные сведения' }), { target: { value: 'Дополнение' } })
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить задание' }))
  expect(await screen.findByText(/Данные изменились/)).toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: 'Дополнительные сведения' })).toHaveValue('Дополнение')
})
it('shows the author and time of additions on completed work without editing', async () => {
  vi.mocked(ticketApi.fieldWorkChanges).mockResolvedValue({
    items: [{ id: 'change', author: 'Инженер', created_at: '2026-11-26T09:00:00Z',
      before: { reason: 'До', note: null, visit_address: null },
      after: { reason: 'После', note: null, visit_address: null }, additional_information: 'Уточнение' }],
    page: 1, page_size: 20, total: 1,
  })
  mount({ ...handoff, work_status: 'REPORTED' })
  expect(screen.queryByRole('button', { name: 'Изменить / дополнить задание' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Изменения и дополнения' }))
  expect(await screen.findByText('Уточнение')).toBeInTheDocument()
  expect(screen.getByText('Инженер')).toBeInTheDocument()
})
