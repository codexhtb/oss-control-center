import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { LoginBrandHero } from './LoginBrandHero'

it('renders the original OSS mark and accessible caption', () => {
  const { container } = render(<LoginBrandHero />)
  expect(screen.getByRole('heading', { name: 'Оптические системы связи' })).toBeInTheDocument()
  expect(container.querySelector('.login-hero__mark')).toHaveAttribute('viewBox', '0 0 580 270')
  expect(container.querySelectorAll('.login-hero__module')).toHaveLength(3)
  expect(container.querySelectorAll('.login-hero__node')).toHaveLength(3)
  expect(container.querySelector('.login-hero__module--o .login-hero__face')).toHaveAttribute('r', '72')
  expect(container.querySelector('#oss-mark-face stop[offset="0.42"]')).toHaveAttribute('stop-color', '#7de0c3')
})
