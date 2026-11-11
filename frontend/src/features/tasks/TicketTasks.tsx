import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router'
import { errorText } from '../../shared/api/client'
import type { MeResponse } from '../../shared/api/types'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import { taskApi } from './task-api'
import { TaskConversation } from './TaskConversation'

export function TicketTasks({ ticketId, session }: { ticketId: string; session: MeResponse }) {
  const queryClient = useQueryClient()
  const [search, setSearch] = useSearchParams()
  const [page, setPage] = useState(1)
  const [assigneeId, setAssigneeId] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const selected = search.get('task')
  const canCreate = session.permissions.includes('tasks.create')
  const list = useQuery({ queryKey: ['internal-tasks', ticketId, page], queryFn: ({ signal }) => taskApi.list('all', 'all', page, ticketId, signal), refetchInterval: 15_000 })
  const assignees = useQuery({ queryKey: ['internal-task-assignees', ticketId], queryFn: ({ signal }) => taskApi.assignees(ticketId, signal), enabled: canCreate })

  useEffect(() => {
    if (selected) document.getElementById('ticket-tasks')?.scrollIntoView?.({ block: 'start' })
  }, [selected, ticketId])

  function choose(id: string) {
    setSearch((previous) => { const next = new URLSearchParams(previous); next.set('task', id); return next })
  }

  async function create(event: FormEvent) {
    event.preventDefault()
    if (!assigneeId || !description.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      const task = await taskApi.create({ ticket_id: ticketId, assignee_id: assigneeId, description: description.trim(), client_request_id: crypto.randomUUID() })
      setDescription('')
      setAssigneeId('')
      choose(task.id)
      await queryClient.invalidateQueries({ queryKey: ['internal-tasks'] })
    } catch (cause) { setError(cause) }
    finally { setBusy(false) }
  }

  return <section id="ticket-tasks" className="detail-panel internal-tasks-panel"><div className="detail-panel__heading"><h2>Поручения</h2></div>
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
    {canCreate && <form className="internal-task-create" onSubmit={(event) => void create(event)}><label className="field"><span>Сотрудник</span><select required value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)}><option value="">Выберите сотрудника</option>{assignees.data?.map((user) => <option key={user.id} value={user.id}>{user.display_name}</option>)}</select></label>{assignees.isError && <Notice tone="error">{errorText(assignees.error)}</Notice>}<label className="field internal-task-create__text"><span>Что нужно узнать или сделать?</span><textarea required maxLength={4000} rows={3} value={description} onChange={(event) => setDescription(event.target.value)} /></label><Button type="submit" disabled={busy || !assigneeId || !description.trim()}>Создать поручение</Button></form>}
    {list.isPending && <p role="status">Загружаем поручения…</p>}
    {list.isError && <Notice tone="error">{errorText(list.error)}</Notice>}
    {list.data?.items.length === 0 && <p className="muted">Поручений по заявке нет.</p>}
    {list.data && list.data.items.length > 0 && <div className="internal-task-list">{list.data.items.map((item) => <button type="button" key={item.id} className={`internal-task-list__item${selected === item.id ? ' internal-task-list__item--selected' : ''}`} onClick={() => choose(item.id)}><b>{item.title}</b><span>{item.assignee_name} · {item.status === 'OPEN' ? 'Активно' : 'Завершено'}</span>{item.latest_answer && <span className="internal-task-list__answer">Ответ: {item.latest_answer}</span>}</button>)}</div>}
    {list.data && list.data.total > 20 && <div className="fact-pager"><Button variant="quiet" disabled={page === 1} onClick={() => setPage(page - 1)}>Назад</Button><span>{page} / {Math.ceil(list.data.total / 20)}</span><Button variant="quiet" disabled={page * 20 >= list.data.total} onClick={() => setPage(page + 1)}>Далее</Button></div>}
    {selected && <TaskConversation key={selected} taskId={selected} ticketId={ticketId} session={session} onUpdated={() => void queryClient.invalidateQueries({ queryKey: ['internal-tasks'] })} />}
  </section>
}
