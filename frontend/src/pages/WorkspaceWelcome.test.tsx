import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { MeResponse } from '../shared/api/types'
import { WorkspaceWelcome } from './WorkspaceWelcome'

const user: MeResponse['user'] = {
  id: '00000000-0000-4000-8000-000000000001',
  login: 'synthetic',
  display_name: 'Тестовый оператор',
  role: 'operator',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

afterEach(() => vi.useRealTimers())

it('creates a small flower burst only after clicking the employee name', () => {
  vi.useFakeTimers()
  const { container } = render(<WorkspaceWelcome user={user} />)
  const name = screen.getByRole('button', { name: 'Тестовый оператор' })
  expect(screen.getByRole('heading', { name: /Добро пожаловать, Тестовый оператор/ })).toBeInTheDocument()
  expect(container.querySelectorAll('.workspace-welcome__particle')).toHaveLength(0)

  fireEvent.click(name)
  expect(container.querySelectorAll('.workspace-welcome__particle')).toHaveLength(10)
  fireEvent.click(name)
  expect(container.querySelectorAll('.workspace-welcome__particle')).toHaveLength(10)

  act(() => vi.advanceTimersByTime(1600))
  expect(container.querySelectorAll('.workspace-welcome__particle')).toHaveLength(0)
})
