import { fireEvent, render, screen } from '@testing-library/react'
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

afterEach(() => { vi.unstubAllGlobals(); setSimplifiedEffects(false) })

it.each(['operator', 'engineer', 'admin'] as const)('shows the animated name as plain text for %s', (role) => {
  const { container } = render(<WorkspaceWelcome user={{ ...user, role }} />)
  const name = container.querySelector('.workspace-welcome__name')
  expect(screen.getByRole('heading', { name: /Добро пожаловать, Тестовый оператор/ })).toBeInTheDocument()
  expect(name?.tagName).toBe('SPAN')
  expect([...container.querySelectorAll('.workspace-welcome__name-word')].map((part) => part.textContent)).toEqual(['Тестовый', 'оператор'])
  expect(container.querySelectorAll('.workspace-welcome__name-letter')).toHaveLength(Array.from('Тестовыйоператор').length)
  expect(screen.queryByRole('button', { name: 'Тестовый оператор' })).not.toBeInTheDocument()
  fireEvent.click(name!)
  expect(container.querySelector('.workspace-welcome__burst')).not.toBeInTheDocument()
  expect(container.querySelector('.workspace-welcome__particle')).not.toBeInTheDocument()
})

it('keeps the name text only in simplified effects mode', () => {
  setSimplifiedEffects(true)
  const { container } = render(<WorkspaceWelcome user={user} />)
  fireEvent.click(container.querySelector('.workspace-welcome__name')!)
  expect(screen.queryByRole('button', { name: 'Тестовый оператор' })).not.toBeInTheDocument()
  expect(container.querySelector('.workspace-welcome__burst')).not.toBeInTheDocument()
})

it('keeps the name text only when the system requests reduced motion', () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true }))
  const { container } = render(<WorkspaceWelcome user={user} />)
  fireEvent.click(container.querySelector('.workspace-welcome__name')!)
  expect(screen.queryByRole('button', { name: 'Тестовый оператор' })).not.toBeInTheDocument()
  expect(container.querySelector('.workspace-welcome__burst')).not.toBeInTheDocument()
})

it('limits individually animated letters in long employee names', () => {
  const { container } = render(<WorkspaceWelcome user={{ ...user, display_name: 'А'.repeat(70) }} />)
  expect(container.querySelectorAll('.workspace-welcome__name-letter')).toHaveLength(24)
  expect(screen.getByRole('heading', { name: /А{70}/ })).toBeInTheDocument()
})
