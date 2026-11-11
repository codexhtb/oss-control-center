import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { errorText } from '../../shared/api/client'
import type { MeResponse } from '../../shared/api/types'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import { formatDate } from '../tickets/presentation'
import { taskApi } from './task-api'
import { TaskConversation } from './TaskConversation'

export function TicketTasks({ ticketId, session }: { ticketId: string; session: MeResponse }) {
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [assigneeId, setAssigneeId] = useState('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [due, setDue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const canCreate = session.permissions.includes('tasks.create')
  const list = useQuery({ queryKey: ['internal-tasks', ticketId, page], queryFn: ({ signal }) => taskApi.list('all', 'all', page, ticketId, signal), refetchInterval: 15_000 })
  const assignees = useQuery({ queryKey: ['internal-task-assignees', ticketId], queryFn: ({ signal }) => taskApi.assignees(ticketId, signal), enabled: creating && canCreate })

  async function create(event: FormEvent) {
    event.preventDefault()
    if (!assigneeId || !title.trim() || !description.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      const task = await taskApi.create({ ticket_id: ticketId, assignee_id: assigneeId, title: title.trim(), description: description.trim(), due_at: due ? new Date(`${due}:00+03:00`).toISOString() : null, client_request_id: crypto.randomUUID() })
      setCreating(false)
      setTitle('')
      setDescription('')
      setDue('')
      setAssigneeId('')
      setSelected(task.id)
      await queryClient.invalidateQueries({ queryKey: ['internal-tasks'] })
    } catch (cause) { setError(cause) }
    finally { setBusy(false) }
  }

  return <section className="detail-panel internal-tasks-panel"><div className="detail-panel__heading"><h2>Поручения</h2><div className="user-actions"><span>{list.data?.total ?? '—'}</span>{canCreate && <Button variant="secondary" onClick={() => setCreating((value) => !value)}>{creating ? 'Отмена' : 'Создать поручение'}</Button>}</div></div>
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
    {creating && <form className="internal-task-create" onSubmit={(event) => void create(event)}><label className="field"><span>Исполнитель</span><select required value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)}><option value="">Выберите сотрудника</option>{assignees.data?.map((user) => <option key={user.id} value={user.id}>{user.display_name}</option>)}</select></label>{assignees.isError && <Notice tone="error">{errorText(assignees.error)}</Notice>}<label className="field"><span>Заголовок</span><input required maxLength={160} value={title} onChange={(event) => setTitle(event.target.value)} /></label><label className="field"><span>Текст поручения</span><textarea required maxLength={4000} rows={3} value={description} onChange={(event) => setDescription(event.target.value)} /></label><label className="field"><span>Срок, время Абхазии (необязательно)</span><input type="datetime-local" value={due} onChange={(event) => setDue(event.target.value)} /></label><Button type="submit" disabled={busy || !assigneeId}>Создать</Button></form>}
    {list.isPending && <p role="status">Загружаем поручения…</p>}
    {list.isError && <Notice tone="error">{errorText(list.error)}</Notice>}
    {list.data?.items.length === 0 && <p className="muted">Поручений по заявке нет.</p>}
    {list.data && list.data.items.length > 0 && <div className="internal-task-list">{list.data.items.map((item) => <button type="button" key={item.id} className={`internal-task-list__item${selected === item.id ? ' internal-task-list__item--selected' : ''}`} onClick={() => setSelected(item.id)}><b>{item.title}</b><span>{item.assignee_name} · {item.status === 'OPEN' ? 'Активно' : item.status === 'NO_ANSWER' ? 'Не дозвонилась' : 'Завершено'} · {formatDate(item.updated_at)}</span></button>)}</div>}
    {list.data && list.data.total > 20 && <div className="fact-pager"><Button variant="quiet" disabled={page === 1} onClick={() => setPage(page - 1)}>Назад</Button><span>{page} / {Math.ceil(list.data.total / 20)}</span><Button variant="quiet" disabled={page * 20 >= list.data.total} onClick={() => setPage(page + 1)}>Далее</Button></div>}
    {selected && <TaskConversation key={selected} taskId={selected} session={session} onUpdated={() => void queryClient.invalidateQueries({ queryKey: ['internal-tasks'] })} />}
  </section>
}
