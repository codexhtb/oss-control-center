import type { ApiErrorBody } from './types'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly requestId?: string,
  ) {
    super(code)
    this.name = 'ApiError'
  }
}

export class CsrfUnavailableError extends Error {
  constructor() {
    super('CSRF token is unavailable')
    this.name = 'CsrfUnavailableError'
  }
}

export type ApiMethod = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'

export interface ApiRequestOptions {
  method?: ApiMethod
  json?: unknown
  signal?: AbortSignal
  ifMatch?: string
}

export function csrfCookie(cookieString: string): string | null {
  const entry = cookieString.split(';').map((part) => part.trim()).find((part) => part.startsWith('oss_csrf='))
  if (!entry) return null
  try {
    return decodeURIComponent(entry.slice('oss_csrf='.length)) || null
  } catch {
    return null
  }
}

export async function apiRequest<T>(path: `/api/v1/${string}`, options: ApiRequestOptions = {}): Promise<T> {
  if (!path.startsWith('/api/v1/') || path.includes('://') || path.includes('\\')) {
    throw new Error('Invalid API path')
  }
  const method = options.method ?? 'GET'
  const headers = new Headers({ Accept: 'application/json' })
  if (options.json !== undefined) headers.set('Content-Type', 'application/json')
  if (options.ifMatch) headers.set('If-Match', options.ifMatch)
  if (method !== 'GET' && path !== '/api/v1/auth/login') {
    const token = csrfCookie(document.cookie)
    if (!token) throw new CsrfUnavailableError()
    headers.set('X-CSRF-Token', token)
  }
  const response = await fetch(path, {
    method,
    headers,
    body: options.json === undefined ? undefined : JSON.stringify(options.json),
    signal: options.signal,
    credentials: 'same-origin',
    cache: 'no-store',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
  })
  const isJson = response.headers.get('content-type')?.includes('application/json') ?? false
  const payload: unknown = isJson ? await response.json() : null
  if (!response.ok) {
    const error = isErrorBody(payload) ? payload : null
    throw new ApiError(response.status, error?.code ?? 'api_error', error?.request_id)
  }
  if (!isJson) throw new ApiError(response.status, 'invalid_response')
  return payload as T
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return typeof candidate.code === 'string' && typeof candidate.request_id === 'string'
}

export function errorText(error: unknown): string {
  if (error instanceof CsrfUnavailableError) return 'Сессия требует обновления. Войдите снова.'
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401: return 'Сессия завершена. Войдите снова.'
      case 403: return 'Недостаточно прав или запрос отклонён проверкой безопасности.'
      case 404: return 'Запрашиваемые данные не найдены.'
      case 409: return 'Данные изменились. Обновите страницу и повторите действие.'
      case 422: return 'Проверьте введённые данные.'
      case 429: return 'Слишком много попыток. Повторите позже.'
      case 503: return 'Сервис временно недоступен. Повторите позже.'
      default: return 'Не удалось выполнить запрос.'
    }
  }
  return 'Не удалось связаться с сервером. Проверьте подключение.'
}
