import { apiRequest } from '../../shared/api/client'
import type { LoginResponse, MeResponse } from '../../shared/api/types'

export const authApi = {
  me: (signal?: AbortSignal) => apiRequest<MeResponse>('/api/v1/auth/me', { signal }),
  login: (login: string, password: string) =>
    apiRequest<LoginResponse>('/api/v1/auth/login', {
      method: 'POST',
      json: { login, password },
    }),
  logout: () => apiRequest<{ status: 'logged_out' }>('/api/v1/auth/logout', { method: 'POST' }),
}
