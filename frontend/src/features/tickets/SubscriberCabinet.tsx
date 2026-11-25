import { useQuery } from '@tanstack/react-query'
import { Copy, Eye, EyeOff } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ticketApi } from './ticket-api'

export function SubscriberCabinet({ lookupId, canReadPassword }: { lookupId: string; canReadPassword: boolean }) {
  const login = useQuery({
    queryKey: ['subscriber-cabinet', lookupId],
    queryFn: ({ signal }) => ticketApi.subscriberCabinet(lookupId, signal),
    retry: false,
  })
  // Never use a mutation/query cache, form input or browser storage for the secret.
  const [password, setPassword] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState('')
  const request = useRef<AbortController | null>(null)
  const mayReveal = canReadPassword && login.data?.password_available === true

  function clear() {
    request.current?.abort()
    request.current = null
    setPassword(null)
    setPending(false)
  }

  useEffect(() => {
    const hide = () => { if (document.visibilityState === 'hidden') clear() }
    const pageHide = () => clear()
    document.addEventListener('visibilitychange', hide)
    window.addEventListener('pagehide', pageHide)
    return () => {
      request.current?.abort()
      document.removeEventListener('visibilitychange', hide)
      window.removeEventListener('pagehide', pageHide)
    }
  }, [])

  useEffect(() => {
    if (!mayReveal) clear()
  }, [mayReveal])

  useEffect(() => {
    if (password === null) return
    const timer = window.setTimeout(clear, 60_000)
    return () => window.clearTimeout(timer)
  }, [password])

  async function reveal() {
    if (password !== null) { clear(); return }
    if (!mayReveal || request.current) return
    const controller = new AbortController()
    request.current = controller
    setPending(true)
    setMessage('')
    try {
      const result = await ticketApi.subscriberPassword(lookupId, controller.signal)
      if (!controller.signal.aborted) setPassword(result.password)
    } catch {
      if (!controller.signal.aborted) setMessage('Пароль недоступен. Обновите карточку или обратитесь к администратору.')
    } finally {
      if (request.current === controller) { request.current = null; setPending(false) }
    }
  }

  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value)
      setMessage('Скопировано')
    } catch {
      setMessage('Не удалось скопировать')
    }
  }

  return <div className="subscriber-cabinet" aria-label="Личный кабинет">
    <div className="subscriber-cabinet__row"><span>Логин ЛК</span><strong>{login.data?.login || (login.isPending ? 'Загрузка…' : 'Недоступен')}</strong>
      <button type="button" className="subscriber-cabinet__action" aria-label="Копировать логин" title="Копировать логин" disabled={!login.data?.login} onClick={() => { if (login.data) void copy(login.data.login) }}><Copy size={15} /></button>
    </div>
    <div className="subscriber-cabinet__row"><span>Пароль ЛК</span><strong className="subscriber-cabinet__secret">{password ?? '••••••••'}</strong>
      <button type="button" className="subscriber-cabinet__action" aria-label={password === null ? 'Показать пароль' : 'Скрыть пароль'} title={mayReveal ? password === null ? 'Показать пароль' : 'Скрыть пароль' : 'Просмотр пароля недоступен'} disabled={!mayReveal || pending} aria-expanded={password !== null} onClick={() => void reveal()}>{password === null ? <Eye size={15} /> : <EyeOff size={15} />}</button>
      <button type="button" className="subscriber-cabinet__action" aria-label="Копировать пароль" title="Копировать пароль" disabled={password === null} onClick={() => { if (password !== null) void copy(password) }}><Copy size={15} /></button>
    </div>
    {message && <span role="status" className="subscriber-cabinet__message">{message}</span>}
  </div>
}
