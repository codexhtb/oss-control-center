import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import type { MeResponse } from '../../shared/api/types'
import { errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import { formatDate } from '../tickets/presentation'
import { taskApi, type TaskEventKind } from './task-api'

const eventNames: Record<TaskEventKind, string> = {
  CREATED: 'Поручение создано', MESSAGE: 'Ответ', COMPLETED: 'Поручение завершено',
  NO_ANSWER: 'Не дозвонилась', REOPENED: 'Поручение открыто повторно',
}

export function TaskConversation({ taskId, session, onUpdated }: { taskId: string; session: MeResponse; onUpdated?: () => void }) {
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const task = useQuery({ queryKey: ['internal-task', taskId], queryFn: ({ signal }) => taskApi.get(taskId, signal), refetchInterval: 15_000 })
  const events = useQuery({ queryKey: ['internal-task-events', taskId, page], queryFn: ({ signal }) => taskApi.events(taskId, page, signal), refetchInterval: page === 1 ? 15_000 : false })
  const canReply = task.data?.status === 'OPEN' && (
    (task.data?.creator_id === session.user.id && session.permissions.includes('tasks.create')) ||
    (task.data?.assignee_id === session.user.id && session.permissions.includes('tasks.execute'))
  )
  const canFinish = task.data?.status === 'OPEN' && task.data?.assignee_id === session.user.id && session.permissions.includes('tasks.execute')
  const canReopen = task.data?.status !== 'OPEN' && task.data?.creator_id === session.user.id && session.permissions.includes('tasks.create')

  async function act(kind: 'MESSAGE' | 'COMPLETED' | 'NO_ANSWER' | 'REOPENED') {
    if (!task.data || busy || (kind === 'MESSAGE' && !body.trim())) return
    setBusy(true)
    setError(null)
    try {
      await taskApi.act(taskId, task.data.version, kind, kind === 'MESSAGE' ? body.trim() : null, crypto.randomUUID())
      if (kind === 'MESSAGE') setBody('')
      setPage(1)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['internal-task', taskId] }),
        queryClient.invalidateQueries({ queryKey: ['internal-task-events', taskId] }),
        queryClient.invalidateQueries({ queryKey: ['internal-tasks'] }),
        queryClient.invalidateQueries({ queryKey: ['internal-task-notifications'] }),
      ])
      onUpdated?.()
    } catch (cause) {
      setError(cause)
      await queryClient.invalidateQueries({ queryKey: ['internal-task', taskId] })
    } finally { setBusy(false) }
  }

  if (task.isPending) return <p role="status">Загружаем поручение…</p>
  if (task.isError) return <Notice tone="error">{errorText(task.error)}</Notice>
  const item = task.data
  return <section className="internal-task-conversation" aria-label={`Поручение: ${item.title}`}>
    <div className="internal-task-conversation__heading"><div><h3>{item.title}</h3><p>Заявка <Link to={`/tickets/${item.ticket_id}`}>#{item.ticket_number}</Link> · {item.creator_name} → {item.assignee_name}</p></div><span className={`internal-task-status internal-task-status--${item.status.toLowerCase()}`}>{item.status === 'OPEN' ? 'Активно' : item.status === 'NO_ANSWER' ? 'Не дозвонилась' : 'Завершено'}</span></div>
    <p className="internal-task-description">{item.description}</p>
    {item.due_at && <p className="muted">Срок: {formatDate(item.due_at)}</p>}
    <div className="internal-task-events" aria-label="Переписка">
      {events.isPending && <p>Загружаем историю…</p>}
      {events.isError && <Notice tone="error">{errorText(events.error)}</Notice>}
      {events.data?.items.slice().reverse().map((entry) => <div key={entry.id} className={`internal-task-event${entry.actor_id === session.user.id ? ' internal-task-event--own' : ''}`}><div><b>{entry.actor_name}</b><time dateTime={entry.created_at}>{formatDate(entry.created_at)}</time></div><span>{eventNames[entry.kind]}</span>{entry.body && <p>{entry.body}</p>}</div>)}
      {events.data && events.data.total > 50 && <div className="fact-pager"><Button variant="quiet" disabled={page === 1} onClick={() => setPage(page - 1)}>Новее</Button><span>{page} / {Math.ceil(events.data.total / 50)}</span><Button variant="quiet" disabled={page * 50 >= events.data.total} onClick={() => setPage(page + 1)}>Ранее</Button></div>}
    </div>
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
    {canReply && <form className="internal-task-reply" onSubmit={(event) => { event.preventDefault(); void act('MESSAGE') }}><label className="field"><span>Ответ или уточнение</span><textarea value={body} maxLength={4000} onChange={(event) => setBody(event.target.value)} rows={2} /></label><Button type="submit" disabled={busy || !body.trim()}>Отправить ответ</Button></form>}
    <div className="user-actions">{canFinish && <><Button variant="secondary" disabled={busy} onClick={() => void act('COMPLETED')}>Завершить</Button><Button variant="quiet" disabled={busy} onClick={() => void act('NO_ANSWER')}>Не дозвонилась</Button></>}{canReopen && <Button variant="secondary" disabled={busy} onClick={() => void act('REOPENED')}>Открыть повторно</Button>}</div>
  </section>
}
