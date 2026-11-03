import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { chatApi, type ChatMessage } from './chat-api'

export const chatMessageEvent = 'oss-chat-message'

export function useChatRealtime() {
  const client = useQueryClient()
  const summary = useQuery({
    queryKey: ['chat', 'summary'], queryFn: ({ signal }) => chatApi.summary(signal),
    refetchInterval: 15_000,
  })
  const [startCursor, setStartCursor] = useState<number | null>(null)

  useEffect(() => {
    if (startCursor === null && summary.data) setStartCursor(summary.data.latest_id)
  }, [startCursor, summary.data])

  useEffect(() => {
    if (startCursor === null || typeof EventSource === 'undefined') return
    const stream = new EventSource(`/api/v1/chat/events?after=${startCursor}`)
    stream.addEventListener('message', (event) => {
      try {
        const message = JSON.parse((event as MessageEvent).data) as ChatMessage
        if (!Number.isSafeInteger(message.id) || typeof message.body !== 'string') return
        window.dispatchEvent(new CustomEvent<ChatMessage>(chatMessageEvent, { detail: message }))
        void client.invalidateQueries({ queryKey: ['chat', 'summary'] })
      } catch { /* A malformed event must not stop the stream. */ }
    })
    stream.onopen = () => {
      void client.invalidateQueries({ queryKey: ['chat', 'summary'] })
      void client.invalidateQueries({ queryKey: ['chat', 'latest'] })
    }
    return () => stream.close()
  }, [client, startCursor])

  return summary
}
