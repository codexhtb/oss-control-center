import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import type { MeResponse } from '../shared/api/types'
import { errorText } from '../shared/api/client'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { formatDate } from '../features/tickets/presentation'
import { taskApi } from '../features/tasks/task-api'
import { TaskConversation } from '../features/tasks/TaskConversation'

type Scope = 'assigned' | 'created'
type State = 'active' | 'completed' | 'overdue'

export function TasksPage({ session }: { session: MeResponse }) {
  const [scope, setScope] = useState<Scope>('assigned')
  const [state, setState] = useState<State>('active')
  const [page, setPage] = useState(1)
  const [search, setSearch] = useSearchParams()
  const selected = search.get('task')
  const tasks = useQuery({ queryKey: ['internal-tasks', scope, state, page], queryFn: ({ signal }) => taskApi.list(scope, state, page, undefined, signal), refetchInterval: 15_000 })
  const choose = (id: string) => setSearch({ task: id })
  return <div className="page desk-page tasks-page"><div className="page-heading"><div><p className="eyebrow">SERVICE DESK</p><h1>Задачи</h1></div></div>
    <div className="internal-task-filters"><nav className="reports-presets" aria-label="Участие в поручениях">{([['assigned', 'Мне поручили'], ['created', 'Я поручил']] as const).map(([key, label]) => <button key={key} type="button" className={`reports-preset${scope === key ? ' reports-preset--active' : ''}`} onClick={() => { setScope(key); setPage(1) }}>{label}</button>)}</nav><nav className="reports-presets" aria-label="Состояние поручений">{([['active', 'Активные'], ['completed', 'Завершённые'], ['overdue', 'Просроченные']] as const).map(([key, label]) => <button key={key} type="button" className={`reports-preset${state === key ? ' reports-preset--active' : ''}`} onClick={() => { setState(key); setPage(1) }}>{label}</button>)}</nav></div>
    <div className="internal-task-layout"><section className="detail-panel"><div className="detail-panel__heading"><h2>{scope === 'assigned' ? 'Мне поручили' : 'Я поручил'}</h2><span>{tasks.data?.total ?? '—'}</span></div>
      {tasks.isPending && <p role="status">Загружаем поручения…</p>}{tasks.isError && <Notice tone="error">{errorText(tasks.error)}</Notice>}{tasks.data?.items.length === 0 && <p className="muted">Поручений нет.</p>}
      <div className="internal-task-list">{tasks.data?.items.map((item) => <button key={item.id} type="button" className={`internal-task-list__item${selected === item.id ? ' internal-task-list__item--selected' : ''}`} onClick={() => choose(item.id)}><b>{item.title}</b><span>Заявка #{item.ticket_number} · {scope === 'assigned' ? item.creator_name : item.assignee_name}</span><span>{formatDate(item.updated_at)}{item.due_at ? ` · срок ${formatDate(item.due_at)}` : ''}</span></button>)}</div>
      {tasks.data && tasks.data.total > 20 && <div className="fact-pager"><Button variant="quiet" disabled={page === 1} onClick={() => setPage(page - 1)}>Назад</Button><span>{page} / {Math.ceil(tasks.data.total / 20)}</span><Button variant="quiet" disabled={page * 20 >= tasks.data.total} onClick={() => setPage(page + 1)}>Далее</Button></div>}
    </section><section className="detail-panel">{selected ? <TaskConversation key={selected} taskId={selected} session={session} /> : <p className="muted">Выберите поручение. Создать новое можно из <Link to="/tickets">карточки заявки</Link>.</p>}</section></div>
  </div>
}
