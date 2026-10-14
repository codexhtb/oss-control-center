// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { isAllowedDevApiRequest } from '../../vite.config'

describe('loopback development proxy', () => {
  it('accepts only its own browser origin for mutations', () => {
    expect(isAllowedDevApiRequest('POST', '127.0.0.1:5173', 'http://127.0.0.1:5173')).toBe(true)
    expect(isAllowedDevApiRequest('POST', '127.0.0.1:5173', 'https://evil.example')).toBe(false)
    expect(isAllowedDevApiRequest('POST', '127.0.0.1:5173', undefined)).toBe(false)
    expect(isAllowedDevApiRequest('GET', 'evil.example', undefined)).toBe(false)
  })
})
