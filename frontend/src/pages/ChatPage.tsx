import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowDown, LoaderCircle, Send, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { chatDeletedEvent, chatMessageEvent } from '../features/chat/ChatRealtime'
import { chatApi, type ChatMessage } from '../features/chat/chat-api'
import { errorText } from '../shared/api/client'
import type { User } from '../shared/api/types'

function mergeMessages(current: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const byId = new Map(current.map((item) => [item.id, item]))
  incoming.forEach((item) => byId.set(item.id, item))
  return [...byId.values()].sort((a, b) => a.id - b.id)
}

const timeFormat = new Intl.DateTimeFormat('ru', { hour: '2-digit', minute: '2-digit' })
const dayFormat = new Intl.DateTimeFormat('ru', { day: 'numeric', month: 'long', year: 'numeric' })

export function ChatPage({ user, canDelete = false }: { user: User; canDelete?: boolean }) {
  const client = useQueryClient()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [olderBefore, setOlderBefore] = useState<number | null>(null)
  const [body, setBody] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [nearBottom, setNearBottom] = useState(true)
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const bottomRef = useRef(true)
  const pendingScroll = useRef<{ height: number; top: number } | null>(null)
  const lastRead = useRef(0)
  const deletedIds = useRef(new Set<number>())

  const markRead = useCallback((id: number) => {
    if (id <= lastRead.current || !bottomRef.current) return
    lastRead.current = id
    void chatApi.markRead(id).then(() => client.invalidateQueries({ queryKey: ['chat', 'summary'] })).catch(() => {
      lastRead.current = 0
    })
  }, [client])

  const refresh = useCallback(async () => {
    try {
      const page = await chatApi.history()
      setMessages((current) => mergeMessages(current, page.items.filter((item) => !deletedIds.current.has(item.id))))
      setOlderBefore((current) => current === null ? page.next_before : current)
      setLoadError(null)
    } catch (error) {
      setLoadError(errorText(error))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    const receive = (event: Event) => {
      const item = (event as CustomEvent<ChatMessage>).detail
      if (!deletedIds.current.has(item.id)) setMessages((current) => mergeMessages(current, [item]))
    }
    const receiveDeletion = (event: Event) => {
      const id = (event as CustomEvent<number>).detail
      deletedIds.current.add(id)
      setMessages((current) => current.filter((item) => item.id !== id))
    }
    window.addEventListener(chatMessageEvent, receive)
    window.addEventListener(chatDeletedEvent, receiveDeletion)
    return () => {
      window.removeEventListener(chatMessageEvent, receive)
      window.removeEventListener(chatDeletedEvent, receiveDeletion)
    }
  }, [])
  useEffect(() => {
    const onFocus = () => { void refresh() }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [refresh])

  const newestId = messages.at(-1)?.id ?? 0
  useLayoutEffect(() => {
    const input = inputRef.current
    if (!input) return
    input.style.height = 'auto'
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`
  }, [body])
  useLayoutEffect(() => {
    const list = listRef.current
    if (!list) return
    if (pendingScroll.current) {
      list.scrollTop = list.scrollHeight - pendingScroll.current.height + pendingScroll.current.top
      pendingScroll.current = null
    } else if (bottomRef.current) {
      list.scrollTop = list.scrollHeight
    }
    if (bottomRef.current && newestId) markRead(newestId)
  }, [messages, newestId, markRead])

  const onScroll = () => {
    const list = listRef.current
    if (!list) return
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 100
    bottomRef.current = atBottom
    setNearBottom(atBottom)
    if (atBottom && newestId) markRead(newestId)
  }

  const loadOlder = async () => {
    const list = listRef.current
    if (!olderBefore || !list || loadingOlder) return
    setLoadingOlder(true)
    pendingScroll.current = { height: list.scrollHeight, top: list.scrollTop }
    try {
      const page = await chatApi.history(olderBefore)
      setMessages((current) => mergeMessages(current, page.items.filter((item) => !deletedIds.current.has(item.id))))
      setOlderBefore(page.next_before)
      setLoadError(null)
    } catch (error) {
      pendingScroll.current = null
      setLoadError(errorText(error))
    } finally { setLoadingOlder(false) }
  }

  const send = useMutation({
    mutationFn: ({ text, id }: { text: string; id: string }) => chatApi.send(text, id),
    onSuccess: (item) => {
      bottomRef.current = true
      setNearBottom(true)
      setMessages((current) => mergeMessages(current, [item]))
      void client.invalidateQueries({ queryKey: ['chat', 'summary'] })
    },
  })
  const remove = useMutation({
    mutationFn: (id: number) => chatApi.remove(id),
    onSuccess: (_, id) => {
      deletedIds.current.add(id)
      setMessages((current) => current.filter((item) => item.id !== id))
      void client.invalidateQueries({ queryKey: ['chat', 'summary'] })
    },
  })

  const submit = () => {
    const text = body.trim()
    if (!text || text.length > 2000 || send.isPending) return
    const id = crypto.randomUUID()
    send.mutate({ text, id }, { onSuccess: () => setBody((current) => current === body ? '' : current) })
  }
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  let previousDay = ''
  let previousMessage: ChatMessage | null = null
  return <section className="staff-chat" aria-label="Общий чат сотрудников">
    <header className="staff-chat__header"><h1>Общий чат</h1></header>
    <div className="staff-chat__history">
    <div className="staff-chat__timeline" ref={listRef} onScroll={onScroll} role="log" aria-live="polite" aria-relevant="additions">
      {olderBefore && <button className="staff-chat__older" type="button" onClick={() => void loadOlder()} disabled={loadingOlder}>{loadingOlder ? 'Загружаем…' : 'Показать ранние сообщения'}</button>}
      {loading && <div className="staff-chat__state"><LoaderCircle className="spin" size={22} /> Загружаем переписку…</div>}
      {!loading && messages.length === 0 && !loadError && <div className="staff-chat__empty"><strong>Переписка начинается здесь</strong><span>Напишите первое сообщение команде.</span></div>}
      {loadError && <div className="staff-chat__state" role="alert">Не удалось загрузить переписку: {loadError} <button type="button" onClick={() => void refresh()}>Повторить</button></div>}
      {remove.isError && <div className="staff-chat__moderation-error" role="alert">Не удалось удалить сообщение: {errorText(remove.error)}</div>}
      {messages.map((message) => {
        const own = message.sender_id === user.id
        const day = dayFormat.format(new Date(message.created_at))
        const showDay = day !== previousDay
        const grouped = !showDay && previousMessage !== null &&
          previousMessage.sender_id === message.sender_id &&
          new Date(message.created_at).getTime() - new Date(previousMessage.created_at).getTime() < 300_000
        previousDay = day
        previousMessage = message
        return <div key={message.id}>
          {showDay && <div className="staff-chat__date"><span>{day}</span></div>}
          <article className={`staff-chat__message ${own ? 'staff-chat__message--own' : ''} ${grouped ? 'staff-chat__message--grouped' : ''}`}>
            {!own && !grouped && <div className="staff-chat__avatar" aria-hidden="true">{message.sender_name.trim().slice(0, 1).toLocaleUpperCase('ru')}</div>}
            <div className="staff-chat__bubble">
              {!grouped && <div className="staff-chat__meta"><strong>{own ? 'Вы' : message.sender_name}</strong><time dateTime={message.created_at}>{timeFormat.format(new Date(message.created_at))}</time></div>}
              <p>{message.body}{grouped && <time className="staff-chat__inline-time" dateTime={message.created_at}>{timeFormat.format(new Date(message.created_at))}</time>}</p>
            </div>
            {canDelete && <button type="button" className="staff-chat__delete" aria-label={`Удалить сообщение ${message.id}`} title="Удалить сообщение" disabled={remove.isPending} onClick={() => { if (window.confirm('Удалить сообщение из общего чата?')) remove.mutate(message.id) }}><Trash2 size={15} /></button>}
          </article>
        </div>
      })}
    </div>
    {!nearBottom && <button type="button" className="staff-chat__jump" onClick={() => { const list = listRef.current; if (list) list.scrollTop = list.scrollHeight; onScroll() }} aria-label="К последним сообщениям"><ArrowDown size={18} /></button>}
    </div>
    <form className="staff-chat__composer" onSubmit={(event) => { event.preventDefault(); submit() }}>
      <label className="sr-only" htmlFor="staff-chat-input">Сообщение</label>
      <textarea ref={inputRef} id="staff-chat-input" value={body} onChange={(event) => setBody(event.target.value)} onKeyDown={onKeyDown} maxLength={2000} rows={1} placeholder="Сообщение команде…" />
      <button type="submit" disabled={!body.trim() || send.isPending} aria-label="Отправить сообщение"><Send size={19} /></button>
      <span className="staff-chat__hint">Enter — отправить · Shift+Enter — новая строка</span>
      {send.isError && <div className="staff-chat__send-error" role="alert">Не удалось отправить: {errorText(send.error)}. Текст сохранён для повтора.</div>}
    </form>
  </section>
}
