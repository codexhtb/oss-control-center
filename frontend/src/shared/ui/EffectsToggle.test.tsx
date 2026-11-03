import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { EffectsToggle } from './EffectsToggle'
import { setSimplifiedEffects } from './effects-preference'

afterEach(() => setSimplifiedEffects(false))

it('saves the simplified effects choice and updates the document immediately', () => {
  setSimplifiedEffects(false)
  const { unmount } = render(<EffectsToggle />)
  const setting = screen.getByRole('checkbox', { name: 'Упрощённые эффекты' })
  expect(setting).not.toBeChecked()
  fireEvent.click(setting)
  expect(setting).toBeChecked()
  expect(window.localStorage.getItem('oss-simplified-effects')).toBe('true')
  expect(document.documentElement.dataset.effects).toBe('simple')
  unmount()
  render(<EffectsToggle />)
  expect(screen.getByRole('checkbox', { name: 'Упрощённые эффекты' })).toBeChecked()
})
