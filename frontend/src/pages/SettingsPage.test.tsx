import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { beforeEach, expect, it } from 'vitest'
import type { MeResponse } from '../shared/api/types'
import { SettingsPage } from './SettingsPage'

const session: MeResponse = {
  user: { id: 'user-1', login: 'admin', display_name: 'Администратор', role: 'admin', is_active: true,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
  permissions: ['users.manage', 'tickets.read.all'],
}

beforeEach(() => window.localStorage.clear())

it('opens the existing catalogs and persists only a personal queue choice', () => {
  const view = render(<MemoryRouter><SettingsPage session={session} /></MemoryRouter>)
  expect(screen.getByRole('link', { name: /Темы обращений/ })).toHaveAttribute('href', '/users?section=categories')
  expect(screen.getByRole('link', { name: /Сотрудники и разрешения/ })).toHaveAttribute('href', '/users?section=employees')
  fireEvent.change(screen.getByLabelText('Очередь по умолчанию'), { target: { value: 'callback' } })
  expect(window.localStorage.getItem('oss.ticket.default-queue.user-1')).toBe('callback')
  view.unmount()
  render(<MemoryRouter><SettingsPage session={session} /></MemoryRouter>)
  expect(screen.getByLabelText('Очередь по умолчанию')).toHaveValue('callback')
})

it('does not show administrative controls to a user without users.manage', () => {
  render(<MemoryRouter><SettingsPage session={{ ...session, permissions: ['tickets.read.all'] }} /></MemoryRouter>)
  expect(screen.queryByRole('link', { name: /Темы обращений/ })).not.toBeInTheDocument()
  expect(screen.getByLabelText('Очередь по умолчанию')).toBeInTheDocument()
})
