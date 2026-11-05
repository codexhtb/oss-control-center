export type TicketQueue = 'all' | 'waiting' | 'mine' | 'callback'

const queues: TicketQueue[] = ['all', 'waiting', 'mine', 'callback']
const key = (userId: string) => `oss.ticket.default-queue.${userId}`

export function readDefaultQueue(userId: string): TicketQueue {
  try {
    const value = window.localStorage.getItem(key(userId))
    return queues.includes(value as TicketQueue) ? value as TicketQueue : 'all'
  } catch {
    return 'all'
  }
}

export function saveDefaultQueue(userId: string, queue: TicketQueue): boolean {
  if (!queues.includes(queue)) return false
  try {
    window.localStorage.setItem(key(userId), queue)
    return true
  } catch {
    return false
  }
}
