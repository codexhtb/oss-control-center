import { useQuery } from '@tanstack/react-query'
import type { MeResponse } from '../../shared/api/types'
import { taskApi } from './task-api'

export function useTaskNotifications(session: MeResponse) {
  const enabled = (session.permissions.includes('tickets.read.all') || session.permissions.includes('tickets.read.own'))
    && (session.permissions.includes('tasks.create') || session.permissions.includes('tasks.execute'))
  return useQuery({
    queryKey: ['internal-task-notifications', session.user.id],
    queryFn: ({ signal }) => taskApi.notifications(signal),
    enabled, refetchInterval: 15_000, refetchOnWindowFocus: true,
  })
}
