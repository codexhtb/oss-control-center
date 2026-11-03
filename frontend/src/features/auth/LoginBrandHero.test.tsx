import { render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { setSimplifiedEffects } from '../../shared/ui/effects-preference'
import { LoginBrandHero } from './LoginBrandHero'

afterEach(() => { setSimplifiedEffects(false); vi.unstubAllGlobals() })

it('renders three assembled OSS pieces and keeps the sign accessible', () => {
  const { container } = render(<LoginBrandHero />)
  expect(screen.getByRole('heading', { name: 'Оптические системы связи' })).toBeInTheDocument()
  expect(container.querySelectorAll('.login-hero__piece')).toHaveLength(3)
  expect(container.querySelector('.login-hero__mark')).toHaveAttribute('viewBox', '0 0 560 280')
  expect(container.querySelector('.login-hero')).toHaveAttribute('data-performance', 'full')
})

it('uses the static fallback for the saved simplified setting', () => {
  setSimplifiedEffects(true)
  const { container } = render(<LoginBrandHero />)
  expect(container.querySelector('.login-hero')).toHaveAttribute('data-performance', 'lite')
})

it('uses the static fallback for reduced motion and low-resource devices', () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true }))
  const reduced = render(<LoginBrandHero />)
  expect(reduced.container.querySelector('.login-hero')).toHaveAttribute('data-performance', 'lite')
  reduced.unmount()
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false }))
  vi.stubGlobal('navigator', { ...navigator, hardwareConcurrency: 2 })
  const lowResource = render(<LoginBrandHero />)
  expect(lowResource.container.querySelector('.login-hero')).toHaveAttribute('data-performance', 'lite')
})
