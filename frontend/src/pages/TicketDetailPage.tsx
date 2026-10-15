import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, ClipboardCheck, Clock3, MessageSquareText, RefreshCw, Wrench } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ApiError, errorText } from '../shared/api/client'
import type { MeResponse } from '../shared/api/types'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { ticketApi } from '../features/tickets/ticket-api'
import { MonitoringTicketSection } from '../features/tickets/MonitoringTicketSection'
import { isDiagnosticReport, MonitoringReport } from '../features/tickets/MonitoringReport'
import { eventLabel, formatDate, priorityLabel, statusLabel, ticketTitle, typeLabel } from '../features/tickets/presentation'

function Pager({ page, total, change }: { page: number; total: number; change: (page: number) => void }) {
  if (total <= 20) return null
  return <div className="fact-pager"><span>Страница {page} из {Math.ceil(total / 20)}</span><div><Button variant="quiet" disabled={page === 1} onClick={() => change(page - 1)}>Назад</Button><Button variant="quiet" disabled={page * 20 >= total} onClick={() => change(page + 1)}>Далее</Button></div></div>
}

function EditedFields({ data }: { data: Record<string, unknown> }) {
  const changes = data.changes
  if (!changes || typeof changes !== 'object' || Array.isArray(changes)) return null
  return <div className="history-changes">{Object.entries(changes).filter(([field]) => field === 'subject' || field === 'description').map(([field, entry]) => {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null
    const values = entry as Record<string, unknown>
    const before = typeof values.before === 'string' ? values.before : 'Не указано'
    const after = typeof values.after === 'string' ? values.after : 'Не указано'
    return <p key={field}><b>{field === 'subject' ? 'Тема' : 'Описание'}:</b> {before} → {after}</p>
  })}</div>
}

export function TicketDetailPage({ session }: { session: MeResponse }) {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const decisionInput = useRef<HTMLTextAreaElement>(null)
  const deleteTrigger = useRef<HTMLButtonElement>(null)
  const deleteReasonInput = useRef<HTMLTextAreaElement>(null)
  const [commentPage, setCommentPage] = useState(1)
  const [historyPage, setHistoryPage] = useState(1)
  const [diagnosticPage, setDiagnosticPage] = useState(1)
  const [decisionPage, setDecisionPage] = useState(1)
  const [comment, setComment] = useState('')
  const [finding, setFinding] = useState('')
  const [decisionText, setDecisionText] = useState('')
  const [performedActions, setPerformedActions] = useState('')
  const [fieldReason, setFieldReason] = useState('')
  const [engineerId, setEngineerId] = useState('')
  const [selectedDecision, setSelectedDecision] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)
  const [actionSuccess, setActionSuccess] = useState('')
  const [subscriberBusy, setSubscriberBusy] = useState(false)
  const [subscriberError, setSubscriberError] = useState<unknown>(null)
  const [editing, setEditing] = useState(false)
  const [editSubject, setEditSubject] = useState('')
  const [editDescription, setEditDescription] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteReason, setDeleteReason] = useState('')
  useEffect(() => { if (confirmDelete) deleteReasonInput.current?.focus() }, [confirmDelete])
  function cancelDelete() {
    setConfirmDelete(false)
    window.requestAnimationFrame(() => deleteTrigger.current?.focus())
  }
  const canAssign = session.permissions.includes('tickets.assign')
  const canStart = session.permissions.includes('tickets.start')
  const canDiagnose = session.permissions.includes('tickets.diagnose')
  const canReadDiagnostics = session.permissions.includes('diagnostics.read')
  const canDecide = session.permissions.includes('tickets.resolve')
  const canClose = session.permissions.includes('tickets.close')
  const canComment = session.permissions.includes('tickets.comment')

  const ticketQuery = useQuery({ queryKey: ['ticket', id], queryFn: ({ signal }) => ticketApi.get(id, signal), enabled: !!id })
  const related = !!id && ticketQuery.isSuccess
  const comments = useQuery({ queryKey: ['ticket-comments', id, commentPage], queryFn: ({ signal }) => ticketApi.comments(id, commentPage, signal), enabled: related })
  const history = useQuery({ queryKey: ['ticket-history', id, historyPage], queryFn: ({ signal }) => ticketApi.history(id, historyPage, signal), enabled: related })
  const diagnostics = useQuery({ queryKey: ['ticket-diagnostics', id, diagnosticPage], queryFn: ({ signal }) => ticketApi.diagnostics(id, diagnosticPage, signal), enabled: related && canReadDiagnostics })
  const decisions = useQuery({ queryKey: ['ticket-decisions', id, decisionPage], queryFn: ({ signal }) => ticketApi.decisions(id, decisionPage, signal), enabled: related })
  const engineers = useQuery({ queryKey: ['assignable-engineers'], queryFn: ({ signal }) => ticketApi.engineers(signal), enabled: related && canAssign })

  async function runAction(name: string, action: () => Promise<unknown>, after?: () => void) {
    if (busy) return
    setBusy(name)
    setActionError(null)
    setActionSuccess('')
    try {
      await action()
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['ticket', id] }),
        queryClient.invalidateQueries({ queryKey: ['ticket-comments', id] }),
        queryClient.invalidateQueries({ queryKey: ['ticket-history', id] }),
        queryClient.invalidateQueries({ queryKey: ['ticket-diagnostics', id] }),
        queryClient.invalidateQueries({ queryKey: ['ticket-decisions', id] }),
        queryClient.invalidateQueries({ queryKey: ['tickets'] }),
        queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
      ])
      after?.()
      setActionSuccess('Изменение сохранено в заявке.')
    } catch (error) {
      setActionError(error)
      if (error instanceof ApiError && error.status === 409) {
        await queryClient.invalidateQueries({ queryKey: ['ticket', id] })
      }
    } finally {
      setBusy(null)
    }
  }

  async function openSubscriber(accountNo: string) {
    if (subscriberBusy) return
    setSubscriberBusy(true)
    setSubscriberError(null)
    try {
      const card = await ticketApi.lookup(accountNo)
      navigate(`/subscribers/${card.lookup_id}`)
    } catch (error) {
      setSubscriberError(error)
    } finally {
      setSubscriberBusy(false)
    }
  }

  if (ticketQuery.isPending) return <div className="desk-state" role="status">Открываем заявку…</div>
  if (ticketQuery.isError) return <div className="page desk-page"><Link className="back-link" to="/tickets"><ArrowLeft size={15} /> К реестру</Link><Notice tone="error">{errorText(ticketQuery.error)}</Notice></div>
  const ticket = ticketQuery.data
  const canEdit = ticket.status !== 'CLOSED' && (session.permissions.includes('tickets.edit.all') || (session.permissions.includes('tickets.edit.own') && ticket.created_by === session.user.id))
  const canDelete = session.permissions.includes('tickets.delete.all') || (session.permissions.includes('tickets.delete.own') && ticket.created_by === session.user.id)
  const open = ticket.status !== 'CLOSED'
  const inProgress = ticket.status === 'IN_PROGRESS'
  const assignedName = ticket.assigned_engineer_name || engineers.data?.items.find((item) => item.id === ticket.assigned_engineer_id)?.display_name

  async function deleteCurrent() {
    if (busy) return
    setBusy('delete'); setActionError(null)
    try {
      await ticketApi.remove(id, ticket.version, deleteReason.trim() || undefined)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['tickets'] }),
        queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
      ])
      navigate('/tickets', { replace: true })
    } catch (error) { setActionError(error) }
    finally { setBusy(null) }
  }

  function submitComment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!comment.trim()) return
    void runAction('comment', () => ticketApi.addComment(id, comment.trim()), () => {
      setComment('')
      setCommentPage(Math.ceil(((comments.data?.total || 0) + 1) / 20))
    })
  }

  function submitDiagnostic(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!finding.trim()) return
    void runAction('diagnostic', () => ticketApi.addDiagnostic(id, finding.trim(), ticket.connection_snapshot_id || undefined), () => {
      setFinding('')
      setDiagnosticPage(Math.ceil(((diagnostics.data?.total || 0) + 1) / 20))
    })
  }

  function submitDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!decisionText.trim() || !performedActions.trim()) return
    void runAction('decision', async () => {
      const decision = await ticketApi.addDecision(id, decisionText.trim(), performedActions.trim())
      setSelectedDecision(decision.id)
    }, () => {
      setDecisionText('')
      setPerformedActions('')
      setDecisionPage(Math.ceil(((decisions.data?.total || 0) + 1) / 20))
    })
  }

  return (
    <div className="page desk-page">
      <Link to="/tickets" className="back-link"><ArrowLeft size={15} /> К реестру заявок</Link>
      <div className="ticket-detail-heading"><div><p className="eyebrow">SERVICEDESK / ЗАЯВКА #{ticket.number}</p><h1>{ticketTitle(ticket.subject)}</h1><div className="ticket-detail-tags"><span className="type-label">{typeLabel[ticket.ticket_type]}</span><span className={`status-chip status-chip--${ticket.status.toLowerCase()}`}>{statusLabel[ticket.status]}</span><span className={`priority priority--${ticket.priority.toLowerCase()}`}>{priorityLabel[ticket.priority]}</span></div></div><Button variant="quiet" icon={<RefreshCw size={15} />} onClick={() => void ticketQuery.refetch()} disabled={ticketQuery.isFetching}>Обновить</Button></div>
      {actionError !== null && <Notice tone="error">{errorText(actionError)}{actionError instanceof ApiError && actionError.status === 409 ? ' Карточка обновлена; проверьте версию перед повтором.' : ''}</Notice>}
      {actionSuccess && <Notice>{actionSuccess}</Notice>}
      <div className="ticket-detail-layout">
        <div className="ticket-detail-main">
          {session.permissions.includes('diagnostics.run') && <MonitoringTicketSection ticket={ticket} />}
          {ticket.subscriber && <section className="detail-panel"><div className="detail-panel__heading"><h2>Абонент и подключение</h2><span>{ticket.subscriber.source} · {formatDate(ticket.subscriber.fetched_at)}</span></div><div className="subscriber-summary"><strong>{ticket.subscriber.full_name || ticket.subscriber.organization_name || 'Имя не указано'}</strong><span>ЛС {ticket.account_no}</span>{ticket.selected_service_id && <span>Услуга: {ticket.selected_service_id}</span>}{ticket.selected_connection?.device && <span>Устройство: {ticket.selected_connection.device}</span>}{ticket.selected_connection?.port && <span>Порт: {ticket.selected_connection.port}</span>}{ticket.selected_connection?.vlan && <span>VLAN: {ticket.selected_connection.vlan}</span>}{ticket.selected_connection?.access_type && ticket.selected_connection.access_type !== 'UNKNOWN' && <span>Технология: {ticket.selected_connection.access_type}</span>}{ticket.selected_connection?.onu_id && <span>ONU: {ticket.selected_connection.onu_id}</span>}{ticket.account_no && session.permissions.includes('subscribers.read') && <Button variant="secondary" disabled={subscriberBusy} onClick={() => void openSubscriber(ticket.account_no!)}>Открыть карточку абонента <ArrowRight size={15} /></Button>}{subscriberError !== null && <Notice tone="error">{errorText(subscriberError)}</Notice>}</div></section>}
          <section className="detail-panel"><div className="detail-panel__heading"><h2>Описание</h2><span>Создана {formatDate(ticket.created_at)}</span></div>
            {editing ? <form className="inline-form" onSubmit={(event) => { event.preventDefault(); void runAction('edit', () => ticketApi.edit(id, ticket.version, editSubject.trim(), editDescription.trim()), () => setEditing(false)) }}>
              <label className="field"><span>Тема</span><input value={editSubject} maxLength={160} onChange={(event) => setEditSubject(event.target.value)} required /></label>
              <label className="field"><span>Описание</span><textarea value={editDescription} maxLength={10000} rows={5} onChange={(event) => setEditDescription(event.target.value)} required /></label>
              <div className="user-actions"><Button type="submit" disabled={!!busy || !editSubject.trim() || !editDescription.trim()}>Сохранить изменения</Button><Button variant="quiet" type="button" onClick={() => setEditing(false)}>Отмена</Button></div>
            </form> : <><p className="ticket-description">{ticket.description}</p>{canEdit && <Button variant="secondary" onClick={() => { setEditSubject(ticket.subject || ''); setEditDescription(ticket.description); setEditing(true) }}>Редактировать заявку</Button>}</>}
          </section>
          {!ticket.subscriber && <div className="unlinked-note">Заявка без привязки к абоненту.</div>}
          <section className="detail-panel"><div className="detail-panel__heading"><h2><MessageSquareText size={17} /> Комментарии</h2><span>{comments.data?.total ?? '—'} записей</span></div>{comments.isPending ? <p className="muted">Загружаем комментарии…</p> : comments.isError ? <Notice tone="error">{errorText(comments.error)}</Notice> : comments.data.items.length ? <div className="fact-list">{comments.data.items.map((item) => <article className="fact-item" key={item.id}><div><strong>{item.author_name || "Сотрудник"}</strong><time>{formatDate(item.created_at)}</time></div><p>{item.body}</p></article>)}</div> : <p className="empty-facts">Комментариев пока нет.</p>}<Pager page={commentPage} total={comments.data?.total || 0} change={setCommentPage} />{canComment && open && <form className="inline-form" onSubmit={submitComment}><label className="field"><span>Добавить сведения</span><textarea aria-label="Новый комментарий" value={comment} maxLength={10000} rows={3} onChange={(event) => setComment(event.target.value)} /></label><Button type="submit" disabled={!comment.trim() || !!busy}>Сохранить комментарий</Button></form>}</section>
          {canReadDiagnostics && <section className="detail-panel"><div className="detail-panel__heading"><h2><Wrench size={17} /> Записи диагностики</h2><span>{diagnostics.data?.total ?? '—'} записей</span></div>{diagnostics.isPending ? <p className="muted">Загружаем наблюдения…</p> : diagnostics.isError ? <Notice tone="error">{errorText(diagnostics.error)}</Notice> : diagnostics.data.items.length ? <div className="fact-list">{diagnostics.data.items.map((item) => <article className="fact-item" key={item.id}><div><strong>{item.source_kind === 'AUTOMATED' ? 'Проверка мониторинга' : 'Ручная проверка'}</strong><time>{formatDate(item.observed_at)}</time></div><div>{isDiagnosticReport(item.observation) ? <details className="diagnostic-details"><summary>Результат проверки оборудования</summary><MonitoringReport report={item.observation} /></details> : item.observation.finding || 'Результат не указан'}</div></article>)}</div> : <p className="empty-facts">Записей пока нет.</p>}<Pager page={diagnosticPage} total={diagnostics.data?.total || 0} change={setDiagnosticPage} />{canDiagnose && inProgress && <form className="inline-form" onSubmit={submitDiagnostic}><label className="field"><span>Наблюдение инженера</span><textarea aria-label="Наблюдение инженера" value={finding} maxLength={10000} rows={3} onChange={(event) => setFinding(event.target.value)} /></label><Button type="submit" disabled={!finding.trim() || !!busy}>Записать диагностику</Button></form>}</section>}
          <section className="detail-panel"><div className="detail-panel__heading"><h2><ClipboardCheck size={17} /> Решения</h2><span>{decisions.data?.total ?? '—'} версий</span></div>{decisions.isPending ? <p className="muted">Загружаем решения…</p> : decisions.isError ? <Notice tone="error">{errorText(decisions.error)}</Notice> : decisions.data.items.length ? <div className="fact-list">{decisions.data.items.map((item) => <article className="fact-item" key={item.id}><div><strong>Решение инженера</strong><time>{formatDate(item.created_at)}</time></div><p>{item.decision_text}</p><p className="fact-item__secondary">Выполнено: {item.performed_actions}</p></article>)}</div> : <p className="empty-facts">Решение ещё не зафиксировано.</p>}<Pager page={decisionPage} total={decisions.data?.total || 0} change={setDecisionPage} />{canDecide && inProgress && <form className="inline-form" onSubmit={submitDecision}><label className="field"><span>Итоговое решение</span><textarea ref={decisionInput} aria-label="Итоговое решение" value={decisionText} maxLength={10000} rows={3} onChange={(event) => setDecisionText(event.target.value)} /></label><label className="field"><span>Выполненные действия</span><textarea aria-label="Выполненные действия" value={performedActions} maxLength={10000} rows={3} onChange={(event) => setPerformedActions(event.target.value)} /></label><Button type="submit" disabled={!decisionText.trim() || !performedActions.trim() || !!busy}>Сохранить решение</Button></form>}</section>
          <section className="detail-panel"><div className="detail-panel__heading"><h2><Clock3 size={17} /> История</h2><span>{history.data?.total ?? '—'} событий</span></div>{history.isPending ? <p className="muted">Загружаем историю…</p> : history.isError ? <Notice tone="error">{errorText(history.error)}</Notice> : history.data.items.length ? <ol className="history-list">{history.data.items.map((item) => <li key={item.id}><span className="history-dot" /><div><strong>{eventLabel[item.event_type] || item.event_type}</strong><span>{item.actor_name || "Система"} · версия {item.ticket_version}</span>{item.event_type === 'ticket_edited' && <EditedFields data={item.event_data} />}</div><time>{formatDate(item.occurred_at)}</time></li>)}</ol> : <p className="empty-facts">История пуста.</p>}<Pager page={historyPage} total={history.data?.total || 0} change={setHistoryPage} /></section>
        </div>
        <aside className="ticket-detail-rail ticket-action-rail" aria-label="Управление заявкой">
          <section className="detail-panel ticket-action-panel">
            <header className="ticket-action-panel__header"><h2>Заявка #{ticket.number}</h2><span className="type-label">{typeLabel[ticket.ticket_type]}</span></header>
            <div className="ticket-action-panel__state"><span className={`status-chip status-chip--${ticket.status.toLowerCase()}`}>{statusLabel[ticket.status]}</span><span className={`priority priority--${ticket.priority.toLowerCase()}`}>{priorityLabel[ticket.priority]}</span></div>
            <dl className="ticket-meta">
              <div><dt>Автор</dt><dd>{ticket.created_by_name || 'Сотрудник'}</dd></div>
              <div><dt>Исполнитель</dt><dd>{ticket.assigned_engineer_id ? assignedName || 'Имя не указано' : 'Не назначен'}</dd></div>
              <div><dt>Обновлена</dt><dd><time dateTime={ticket.updated_at}>{formatDate(ticket.updated_at)}</time></dd></div>
            </dl>
            {(open && (canAssign || (canStart && ticket.status === 'OPEN') || (inProgress && (canDecide || canClose || session.permissions.includes('tickets.field_work'))))) && <div className="ticket-action-panel__actions">
              {ticket.status === 'OPEN' && canStart && <Button variant="primary" disabled={!!busy} onClick={() => void runAction('start', () => ticketApi.start(id, ticket.version))}>Взять в работу <ArrowRight size={15} /></Button>}
              {inProgress && canDecide && <Button variant="primary" disabled={!!busy} onClick={() => { decisionInput.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' }); decisionInput.current?.focus({ preventScroll: true }) }}>Записать решение</Button>}
              {open && canAssign && <details className="ticket-action-disclosure"><summary>Назначить инженера</summary>
                {engineers.isPending ? <p className="rail-note">Загружаем инженеров…</p> : engineers.isError ? <Notice tone="error">{errorText(engineers.error)}</Notice> : engineers.data.items.some((item) => item.id !== ticket.assigned_engineer_id) ? <div className="ticket-action-form">
                  <label className="field"><span>Активный инженер</span><select aria-label="Активный инженер" value={engineerId} onChange={(event) => setEngineerId(event.target.value)}><option value="">Выберите инженера</option>{engineers.data.items.filter((item) => item.id !== ticket.assigned_engineer_id).map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
                  <Button disabled={!engineerId || !!busy} onClick={() => void runAction('assign', () => ticketApi.assign(id, engineerId, ticket.version), () => setEngineerId(''))}>Назначить</Button>
                </div> : <p className="rail-note">Нет других активных инженеров.</p>}
              </details>}
              {inProgress && canClose && (selectedDecision || decisions.data?.items.length ? <details className="ticket-action-disclosure"><summary>Закрытие заявки</summary><div className="ticket-action-form">
                <label className="field"><span>Подтверждённое решение</span><select aria-label="Решение для закрытия" value={selectedDecision} onChange={(event) => setSelectedDecision(event.target.value)}><option value="">Выберите решение</option>{decisions.data?.items.map((item) => <option key={item.id} value={item.id}>{formatDate(item.created_at)} · {item.decision_text.slice(0, 36)}</option>)}{selectedDecision && !decisions.data?.items.some((item) => item.id === selectedDecision) && <option value={selectedDecision}>Только что записанное решение</option>}</select></label>
                <Button disabled={!selectedDecision || !!busy} onClick={() => void runAction('close', () => ticketApi.close(id, selectedDecision, ticket.version))}>Закрыть заявку</Button>
              </div></details> : <p className="rail-note">{decisions.isPending ? 'Загружаем решения…' : decisions.isError ? 'Решения недоступны. Обновите карточку.' : 'Для закрытия необходимо записанное решение.'}</p>)}
              {inProgress && session.permissions.includes('tickets.field_work') && <details className="ticket-action-disclosure"><summary>Выездные работы</summary><div className="ticket-action-form">
                <label className="field"><span>Причина передачи</span><textarea aria-label="Причина передачи" rows={3} maxLength={2000} value={fieldReason} onChange={(event) => setFieldReason(event.target.value)} /></label>
                <Button disabled={!fieldReason.trim() || !!busy} onClick={() => void runAction('field', () => ticketApi.requestFieldWork(id, fieldReason.trim(), ticket.version), () => setFieldReason(''))}>Зафиксировать запрос</Button>
              </div></details>}
            </div>}
            {ticket.status === 'CLOSED' && <p className="rail-note">Закрыта {ticket.closed_at ? formatDate(ticket.closed_at) : ''}. История доступна для просмотра.</p>}
            {canDelete && <details className="ticket-action-disclosure ticket-action-panel__more" onToggle={(event) => { if (!event.currentTarget.open) setConfirmDelete(false) }}><summary>Дополнительно</summary>
              {confirmDelete ? <div className="ticket-action-form" role="group" aria-label="Подтверждение удаления" aria-describedby="ticket-delete-warning" onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); cancelDelete() } }}>
                <p id="ticket-delete-warning" className="rail-note">Удалить заявку №{ticket.number} «{ticketTitle(ticket.subject)}»? Её можно будет восстановить из корзины.</p>
                <label className="field"><span>Причина (необязательно)</span><textarea ref={deleteReasonInput} value={deleteReason} maxLength={500} onChange={(event) => setDeleteReason(event.target.value)} /></label>
                <div className="user-actions"><Button className="ticket-danger-action" disabled={!!busy} onClick={() => void deleteCurrent()}>Подтвердить удаление</Button><Button variant="quiet" disabled={!!busy} onClick={cancelDelete}>Отмена</Button></div>
              </div> : <Button ref={deleteTrigger} className="ticket-danger-action" variant="quiet" onClick={() => setConfirmDelete(true)}>Удалить</Button>}
            </details>}
          </section>
        </aside>
      </div>
    </div>
  )
}
