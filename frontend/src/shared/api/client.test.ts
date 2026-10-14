import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiRequest, csrfCookie, CsrfUnavailableError, errorText } from './client'

afterEach(() => vi.unstubAllGlobals())

describe('same-origin API client', () => {
  it('sends no session or CSRF token through JavaScript on login', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ user: { id: 'synthetic' } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await apiRequest('/api/v1/auth/login', { method: 'POST', json: { login: 'synthetic', password: 'test-only' } })
    const [path, options] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(path).toBe('/api/v1/auth/login')
    expect(options.credentials).toBe('same-origin')
    expect(options.cache).toBe('no-store')
    expect(options.redirect).toBe('error')
    expect(new Headers(options.headers).has('X-CSRF-Token')).toBe(false)
  })

  it('requires a CSRF cookie for writes and forwards it as a header', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'logged_out' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(apiRequest('/api/v1/auth/logout', { method: 'POST' })).rejects.toBeInstanceOf(CsrfUnavailableError)
    expect(fetchMock).not.toHaveBeenCalled()

    document.cookie = 'oss_csrf=synthetic%2Dtoken; Path=/'
    await apiRequest('/api/v1/auth/logout', { method: 'POST' })
    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('synthetic-token')
    expect(csrfCookie('other=1; oss_csrf=token-2')).toBe('token-2')
  })

  it('maps structured API errors without exposing server text', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'version_conflict', message: 'Sensitive detail', request_id: 'synthetic-id',
    }), { status: 409, headers: { 'Content-Type': 'application/json' } })))
    await expect(apiRequest('/api/v1/tickets')).rejects.toMatchObject({
      status: 409, code: 'version_conflict', requestId: 'synthetic-id',
    })
    expect(errorText(new ApiError(409, 'version_conflict'))).not.toContain('Sensitive detail')
    expect(errorText(new ApiError(503, 'billing_unavailable'))).toContain('временно недоступен')
  })
})
