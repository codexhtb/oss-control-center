import { QueryClient } from '@tanstack/react-query'

// Private API data stays in memory only while a query is observed.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      staleTime: 0,
      gcTime: 0,
      refetchOnWindowFocus: true,
    },
    mutations: { retry: false },
  },
})
