import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { MeResponse } from '../shared/api/types'
import { WorkspaceWelcome } from './WorkspaceWelcome'
import { setSimplifiedEffects } from '../shared/ui/effects-preference'

const user: MeResponse['user'] = {
  id: '00000000-0000-4000-8000-000000000001',
  login: 'synthetic',
  display_name: 'Тестовый оператор',
  role: 'operator',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); setSimplifiedEffects(false) })

it('creates a small flower burst only after clicking the employee name', () => {
  vi.useFakeTimers()
  const { container } = render(<WorkspaceWelcome user={user} />)
  const name = screen.getByRole('button', { name: 'Тестовый оператор' })
  expect(screen.getByRole('heading', { name: /Добро пожаловать, Тестовый оператор/ })).toBeInTheDocument()
  expect([...container.querySelectorAll('.workspace-welcome__name-word')].map((part) => part.textContent)).toEqual(['Тестовый', 'оператор'])
  expect(container.querySelectorAll('.workspace-welcome__name-letter')).toHaveLength(Array.from('Тестовыйоператор').length)
  expect(container.querySelectorAll('.workspace-welcome__particle')).toHaveLength(0)

  fireEvent.click(name)
  expect(container.querySelectorAll('.workspace-welcome__particle')).toHaveLength(10)
  fireEvent.click(name)
  expect(container.querySelectorAll('.workspace-welcome__particle')).toHaveLength(10)

  act(() => vi.advanceTimersByTime(1600))
  expect(container.querySelectorAll('.workspace-welcome__particle')).toHaveLength(0)
})

it('shows the same animated name without flower interaction for other roles', () => {
  const { container } = render(<WorkspaceWelcome user={{ ...user, role: 'engineer' }} />)
  expect(screen.getByRole('heading', { name: /Добро пожаловать, Тестовый оператор/ })).toBeInTheDocument()
  expect(container.querySelectorAll('.workspace-welcome__name-letter')).toHaveLength(Array.from('Тестовыйоператор').length)
  expect(screen.queryByRole('button', { name: 'Тестовый оператор' })).not.toBeInTheDocument()
  expect(container.querySelectorAll('.workspace-welcome__particle')).toHaveLength(0)
})

it('shows a single lightweight click response for an operator in simplified mode', () => {
  vi.useFakeTimers()
  setSimplifiedEffects(true)
  const { container } = render(<WorkspaceWelcome user={user} />)
  fireEvent.click(screen.getByRole('button', { name: 'Тестовый оператор' }))
  expect(container.querySelector('.workspace-welcome__burst--simple')).toBeInTheDocument()
  expect(container.querySelectorAll('.workspace-welcome__particle')).toHaveLength(0)
  act(() => vi.advanceTimersByTime(650))
  expect(container.querySelector('.workspace-welcome__burst')).not.toBeInTheDocument()
})

it('does not create particles when the system requests reduced motion', () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true }))
  const { container } = render(<WorkspaceWelcome user={user} />)
  fireEvent.click(screen.getByRole('button', { name: 'Тестовый оператор' }))
  expect(container.querySelectorAll('.workspace-welcome__particle')).toHaveLength(0)
  expect(container.querySelector('.workspace-welcome__burst--simple')).toBeInTheDocument()
})

it('limits individually animated letters in long employee names', () => {
  const { container } = render(<WorkspaceWelcome user={{ ...user, display_name: 'А'.repeat(70) }} />)
  expect(container.querySelectorAll('.workspace-welcome__name-letter')).toHaveLength(24)
  expect(screen.getByRole('heading', { name: /А{70}/ })).toBeInTheDocument()
})
