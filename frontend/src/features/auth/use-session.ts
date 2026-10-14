import { useQuery } from '@tanstack/react-query'
import { authApi } from './auth-api'

export const sessionKey = ['session'] as const

export function useSession(revision: number) {
  return useQuery({ queryKey: [...sessionKey, revision], queryFn: ({ signal }) => authApi.me(signal) })
}
