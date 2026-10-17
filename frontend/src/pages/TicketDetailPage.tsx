import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, ClipboardCheck, Clock3, MessageSquareText, RefreshCw, Wrench } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'
import { ApiError, errorText } from '../shared/api/client'
import type { MeResponse } from '../shared/api/types'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { ticketApi } from '../features/tickets/ticket-api'
import { MonitoringTicketSection } from '../features/tickets/MonitoringTicketSection'
import { isDiagnosticReport, MonitoringReport } from '../features/tickets/MonitoringReport'
import { eventLabel, formatDate, priorityLabel, resolutionReasonLabel, statusLabel, ticketTitle, typeLabel } from '../features/tickets/presentation'
import type { CallbackOutcome, ResolutionReason } from '../features/tickets/types'
import { connectionConfiguredIpv4, formatSubscriberAddress } from '../features/tickets/subscriber-presentation'

const callbackLabels: Record<CallbackOutcome, string> = {
  CONFIRMED: 'Проблема устранена', STILL_BROKEN: 'Проблема осталась', NO_ANSWER: 'Не дозвонилась',
}

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
  const location = useLocation()
  const listSearch = typeof location.state?.ticketListSearch === 'string' ? location.state.ticketListSearch : ''
  const listDestination = { pathname: '/tickets', search: listSearch ? `?${listSearch}` : '' }
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const decisionInput = useRef<HTMLTextAreaElement>(null)
  const deleteTrigger = useRef<HTMLButtonElement>(null)
  const deleteReasonInput = useRef<HTMLTextAreaElement>(null)
  const [commentPage, setCommentPage] = useState(1)
  const [historyPage, setHistoryPage] = useState(1)
  const [diagnosticPage, setDiagnosticPage] = useState(1)
  const [decisionPage, setDecisionPage] = useState(1)
  const [callbackPage, setCallbackPage] = useState(1)
  const [callbackComment, setCallbackComment] = useState('')
  const [closeReason, setCloseReason] = useState('')
  const callbackRequest = useRef({ id: crypto.randomUUID(), outcome: '' })
  const [comment, setComment] = useState('')
  const [decisionText, setDecisionText] = useState('')
  const [decisionReason, setDecisionReason] = useState<ResolutionReason>('NOT_ESTABLISHED')
  const [technicalOpen, setTechnicalOpen] = useState(false)
  const [decisionsOpen, setDecisionsOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [callbacksOpen, setCallbacksOpen] = useState(false)
  const [fieldReason, setFieldReason] = useState('')
  const [engineerId, setEngineerId] = useState('')
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
  const canClaim = canStart && session.user.role === 'engineer'
  const canReadDiagnostics = session.permissions.includes('diagnostics.read')
  const canDecide = session.permissions.includes('tickets.resolve')
  const canClose = session.permissions.includes('tickets.close')
  const canCallback = session.permissions.includes('tickets.callback')
  const canComment = session.permissions.includes('tickets.comment')

  const ticketQuery = useQuery({ queryKey: ['ticket', id], queryFn: ({ signal }) => ticketApi.get(id, signal), enabled: !!id })
  const related = !!id && ticketQuery.isSuccess
  const resolved = ticketQuery.data?.status === 'RESOLVED'
  const linked = ticketQuery.data?.ticket_type === 'SUBSCRIBER_LINKED'
  const comments = useQuery({ queryKey: ['ticket-comments', id, commentPage], queryFn: ({ signal }) => ticketApi.comments(id, commentPage, signal), enabled: related })
  const history = useQuery({ queryKey: ['ticket-history', id, historyPage], queryFn: ({ signal }) => ticketApi.history(id, historyPage, signal), enabled: related && historyOpen })
  const diagnostics = useQuery({ queryKey: ['ticket-diagnostics', id, diagnosticPage], queryFn: ({ signal }) => ticketApi.diagnostics(id, diagnosticPage, signal), enabled: related && canReadDiagnostics && technicalOpen })
  const decisions = useQuery({ queryKey: ['ticket-decisions', id, decisionPage], queryFn: ({ signal }) => ticketApi.decisions(id, decisionPage, signal), enabled: related && (decisionsOpen || (resolved && linked)) })
  const callbacks = useQuery({ queryKey: ['ticket-callbacks', id, callbackPage], queryFn: ({ signal }) => ticketApi.callbacks(id, callbackPage, signal), enabled: related && linked && (resolved || callbacksOpen) })
  const engineers = useQuery({ queryKey: ['assignable-engineers'], queryFn: ({ signal }) => ticketApi.engineers(signal), enabled: related && canAssign && ticketQuery.data?.status !== 'CLOSED' && ticketQuery.data?.status !== 'RESOLVED' })

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
        queryClient.invalidateQueries({ queryKey: ['ticket-callbacks', id] }),
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
  if (ticketQuery.isError) return <div className="page desk-page"><Link className="back-link" to={listDestination}><ArrowLeft size={15} /> К реестру</Link><Notice tone="error">{errorText(ticketQuery.error)}</Notice></div>
  const ticket = ticketQuery.data
  const canEdit = ticket.status !== 'CLOSED' && (session.permissions.includes('tickets.edit.all') || (session.permissions.includes('tickets.edit.own') && ticket.created_by === session.user.id))
  const canDelete = session.permissions.includes('tickets.delete.all') || (session.permissions.includes('tickets.delete.own') && ticket.created_by === session.user.id)
  const open = ticket.status !== 'CLOSED'
  const activeWork = ticket.status === 'OPEN' || ticket.status === 'IN_PROGRESS'
  const inProgress = ticket.status === 'IN_PROGRESS'
  const canWriteVerdict = canDecide && (ticket.assigned_engineer_id === session.user.id || session.user.role === 'admin')
  const assignedName = ticket.assigned_engineer_name || engineers.data?.items.find((item) => item.id === ticket.assigned_engineer_id)?.display_name
  const canManualAssign = canAssign && (ticket.assigned_engineer_id === null || session.user.role === 'admin')

  function submitCallback(outcome: CallbackOutcome) {
    if (outcome === 'STILL_BROKEN' && !callbackComment.trim()) return
    if (callbackRequest.current.outcome && callbackRequest.current.outcome !== outcome) {
      callbackRequest.current = { id: crypto.randomUUID(), outcome }
    } else callbackRequest.current.outcome = outcome
    void runAction('callback', () => ticketApi.recordCallback(
      id, outcome, callbackComment.trim() || null, ticket.version, callbackRequest.current.id,
    ), () => {
      callbackRequest.current = { id: crypto.randomUUID(), outcome: '' }
      setCallbackComment('')
      setCallbackPage(1)
    })
  }

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

  function submitDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!decisionText.trim()) return
    void runAction('decision', async () => {
      await ticketApi.addDecision(id, decisionText.trim(), decisionReason)
    }, () => {
      setDecisionText('')
      setDecisionPage(Math.ceil(((decisions.data?.total || 0) + 1) / 20))
    })
  }

  function submitResolve(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!decisionText.trim() || busy) return
    void runAction('resolve', () => ticketApi.resolve(id, decisionText.trim(), decisionReason, ticket.version), () => {
      setDecisionText('')
      setDecisionPage(1)
    })
  }

  return (
    <div className="page desk-page">
      <Link to={listDestination} className="back-link"><ArrowLeft size={15} /> К реестру заявок</Link>
      <div className="ticket-detail-heading"><div><p className="eyebrow">SERVICEDESK / ЗАЯВКА #{ticket.number}</p><h1>{ticketTitle(ticket.subject)}</h1><div className="ticket-detail-tags"><span className="type-label">{typeLabel[ticket.ticket_type]}</span><span className={`status-chip status-chip--${ticket.status.toLowerCase()}`}>{statusLabel[ticket.status]}</span><span className={`priority priority--${ticket.priority.toLowerCase()}`}>{priorityLabel[ticket.priority]}</span></div></div><Button variant="quiet" icon={<RefreshCw size={15} />} onClick={() => void ticketQuery.refetch()} disabled={ticketQuery.isFetching}>Обновить</Button></div>
      {actionError !== null && <Notice tone="error">{actionError instanceof ApiError && actionError.code === 'ticket_already_claimed' ? 'Заявку уже взял другой инженер.' : errorText(actionError)}{actionError instanceof ApiError && actionError.status === 409 ? ' Карточка обновлена; проверьте исполнителя перед повтором.' : ''}</Notice>}
      {actionSuccess && <Notice>{actionSuccess}</Notice>}
      <div className="ticket-detail-layout">
        <div className="ticket-detail-main">
          {session.permissions.includes('diagnostics.run') && <MonitoringTicketSection ticket={ticket} />}
          {ticket.subscriber && <section className="detail-panel"><div className="detail-panel__heading"><h2>Абонент и подключение</h2><span>{ticket.subscriber.source} · {formatDate(ticket.subscriber.fetched_at)}</span></div><div className="subscriber-summary"><strong>{ticket.subscriber.full_name || ticket.subscriber.organization_name || 'Имя не указано'}</strong><span>ЛС {ticket.account_no}</span>{formatSubscriberAddress(ticket.subscriber.actual_address, ticket.subscriber.house_number) && <span>Адрес: {formatSubscriberAddress(ticket.subscriber.actual_address, ticket.subscriber.house_number)}</span>}{ticket.selected_service_id && <span>Услуга: {ticket.selected_service_id}</span>}{ticket.selected_connection?.device && <span>Устройство: {ticket.selected_connection.device}</span>}{ticket.selected_connection?.port && <span>Порт: {ticket.selected_connection.port}</span>}{ticket.selected_connection?.vlan && <span>VLAN: {ticket.selected_connection.vlan}</span>}{connectionConfiguredIpv4(ticket.selected_connection).map((address) => <span key={address}>IPv4 в NetUP: {address}</span>)}{ticket.selected_connection?.access_type && ticket.selected_connection.access_type !== 'UNKNOWN' && <span>Технология: {ticket.selected_connection.access_type}</span>}{ticket.selected_connection?.onu_id && <span>ONU: {ticket.selected_connection.onu_id}</span>}{ticket.account_no && session.permissions.includes('subscribers.read') && <Button variant="secondary" disabled={subscriberBusy} onClick={() => void openSubscriber(ticket.account_no!)}>Открыть карточку абонента <ArrowRight size={15} /></Button>}{subscriberError !== null && <Notice tone="error">{errorText(subscriberError)}</Notice>}</div></section>}
          <section className="detail-panel"><div className="detail-panel__heading"><h2>Описание</h2><span>Создана {formatDate(ticket.created_at)}</span></div>
            {(ticket.category_name || ticket.caller_phone) && <div className="subscriber-summary">{ticket.category_name && <span>Категория при создании: <strong>{ticket.category_name}</strong></span>}{ticket.caller_phone && <span>Телефон входящего звонка: <strong>{ticket.caller_phone}</strong></span>}</div>}
            {editing ? <form className="inline-form" onSubmit={(event) => { event.preventDefault(); void runAction('edit', () => ticketApi.edit(id, ticket.version, editSubject.trim(), editDescription.trim()), () => setEditing(false)) }}>
              <label className="field"><span>Тема</span><input value={editSubject} maxLength={160} onChange={(event) => setEditSubject(event.target.value)} required /></label>
              <label className="field"><span>Описание</span><textarea value={editDescription} maxLength={10000} rows={5} onChange={(event) => setEditDescription(event.target.value)} required /></label>
              <div className="user-actions"><Button type="submit" disabled={!!busy || !editSubject.trim() || !editDescription.trim()}>Сохранить изменения</Button><Button variant="quiet" type="button" onClick={() => setEditing(false)}>Отмена</Button></div>
            </form> : <><p className="ticket-description">{ticket.description}</p>{canEdit && <Button variant="secondary" onClick={() => { setEditSubject(ticket.subject || ''); setEditDescription(ticket.description); setEditing(true) }}>Редактировать заявку</Button>}</>}
          </section>
          {!ticket.subscriber && <div className="unlinked-note">Заявка без привязки к абоненту.</div>}
          <section className="detail-panel"><div className="detail-panel__heading"><h2><MessageSquareText size={17} /> Комментарии</h2><span>{comments.data?.total ?? '—'} записей</span></div>{comments.isPending ? <p className="muted">Загружаем комментарии…</p> : comments.isError ? <Notice tone="error">{errorText(comments.error)}</Notice> : comments.data.items.length ? <div className="fact-list">{comments.data.items.map((item) => <article className="fact-item" key={item.id}><div><strong>{item.author_name || "Сотрудник"}</strong><time>{formatDate(item.created_at)}</time></div><p>{item.body}</p></article>)}</div> : <p className="empty-facts">Комментариев пока нет.</p>}<Pager page={commentPage} total={comments.data?.total || 0} change={setCommentPage} />{canComment && open && <form className="inline-form" onSubmit={submitComment}><label className="field"><span>Добавить сведения</span><textarea aria-label="Новый комментарий" value={comment} maxLength={10000} rows={3} onChange={(event) => setComment(event.target.value)} /></label><Button type="submit" disabled={!comment.trim() || !!busy}>Сохранить комментарий</Button></form>}</section>
          {canReadDiagnostics && <details className="detail-panel detail-disclosure" onToggle={(event) => setTechnicalOpen(event.currentTarget.open)}><summary><Wrench size={17} /> Технические сведения</summary>{technicalOpen && <div className="detail-disclosure__body">{diagnostics.isPending ? <p className="muted">Загружаем сохранённую диагностику…</p> : diagnostics.isError ? <Notice tone="error">{errorText(diagnostics.error)}</Notice> : diagnostics.data?.items.length ? <div className="fact-list">{diagnostics.data.items.map((item) => <article className="fact-item" key={item.id}><div><strong>{item.source_kind === 'AUTOMATED' ? 'Проверка мониторинга' : 'Историческое наблюдение инженера'}</strong><time>{formatDate(item.observed_at)}</time></div><div>{isDiagnosticReport(item.observation) ? <MonitoringReport report={item.observation} /> : item.observation.finding || 'Результат не указан'}</div></article>)}</div> : <p className="empty-facts">Сохранённых технических записей нет.</p>}<Pager page={diagnosticPage} total={diagnostics.data?.total || 0} change={setDiagnosticPage} /></div>}</details>}
          <details className="detail-panel detail-disclosure" onToggle={(event) => setDecisionsOpen(event.currentTarget.open)}><summary><ClipboardCheck size={17} /> Исторические решения</summary>{decisionsOpen && <div className="detail-disclosure__body">{decisions.isPending ? <p className="muted">Загружаем решения…</p> : decisions.isError ? <Notice tone="error">{errorText(decisions.error)}</Notice> : decisions.data?.items.length ? <div className="fact-list">{decisions.data.items.map((item) => <article className="fact-item" key={item.id}><div><strong>{item.id === ticket.resolved_decision_id ? 'Текущий вердикт' : 'Решение инженера'}</strong><time>{formatDate(item.created_at)}</time></div><p>{item.decision_text}</p><p className="fact-item__secondary">{item.engineer_name || 'Инженер'} · {item.reason ? resolutionReasonLabel[item.reason] : 'Причина не указана в исторической записи'}</p>{item.performed_actions && <p className="fact-item__secondary">Ранее указанные действия: {item.performed_actions}</p>}</article>)}</div> : <p className="empty-facts">Решение ещё не зафиксировано.</p>}<Pager page={decisionPage} total={decisions.data?.total || 0} change={setDecisionPage} />{canWriteVerdict && ticket.status === 'RESOLVED' && <form className="inline-form" onSubmit={submitDecision}><label className="field"><span>Причина решения</span><select aria-label="Причина решения" value={decisionReason} onChange={(event) => setDecisionReason(event.target.value as ResolutionReason)}>{Object.entries(resolutionReasonLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="field"><span>Уточнить решение</span><textarea aria-label="Уточнить решение" value={decisionText} maxLength={10000} rows={3} onChange={(event) => setDecisionText(event.target.value)} /></label><Button type="submit" disabled={!decisionText.trim() || !!busy}>Сохранить новую версию</Button></form>}</div>}</details>
          {ticket.ticket_type === 'SUBSCRIBER_LINKED' && ticket.status === 'RESOLVED' && <section className="detail-panel" aria-label="Контрольный обзвон"><div className="detail-panel__heading"><h2>Контрольный обзвон</h2><span>{callbacks.data?.total ?? '—'} попыток</span></div>
            {ticket.status === 'RESOLVED' && <div className="subscriber-summary"><strong>{ticket.subscriber?.full_name || ticket.subscriber?.organization_name || 'Абонент не указан'}</strong><span>ЛС {ticket.account_no}</span><span>Звонящий: {ticket.caller_phone || 'Номер не указан'}</span>{ticket.subscriber?.phone && <span>Контакт NetUP: {ticket.subscriber.phone}</span>}{ticket.subscriber?.contacts?.map((contact, index) => <span key={`${contact.value}-${index}`}>Дополнительный контакт ({contact.type}, {contact.source}): {contact.value}</span>)}<span>Решил: {ticket.resolved_by_name || 'Инженер'} · {ticket.resolved_at ? formatDate(ticket.resolved_at) : 'время не указано'}</span>{decisions.data?.items.find((item) => item.id === ticket.resolved_decision_id) && <p>Вердикт: {decisions.data.items.find((item) => item.id === ticket.resolved_decision_id)?.decision_text}</p>}</div>}
            {callbacks.isPending ? <p className="muted">Загружаем звонки…</p> : callbacks.isError ? <Notice tone="error">{errorText(callbacks.error)}</Notice> : callbacks.data?.items.length ? <div className="fact-list">{callbacks.data.items.map((item) => <article className="fact-item" key={item.id}><div><strong>{callbackLabels[item.outcome]}</strong><time>{formatDate(item.occurred_at)}</time></div><p className="fact-item__secondary">{item.actor_name || 'Сотрудник'}{item.comment ? ` · ${item.comment}` : ''}</p></article>)}</div> : <p className="empty-facts">Звонков пока не было.</p>}<Pager page={callbackPage} total={callbacks.data?.total || 0} change={setCallbackPage} />
          </section>}
          {ticket.ticket_type === 'SUBSCRIBER_LINKED' && ticket.status !== 'RESOLVED' && <details className="detail-panel detail-disclosure" onToggle={(event) => setCallbacksOpen(event.currentTarget.open)}><summary>История контрольных обзвонов</summary>{callbacksOpen && <div className="detail-disclosure__body">{callbacks.isPending ? <p className="muted">Загружаем звонки…</p> : callbacks.isError ? <Notice tone="error">{errorText(callbacks.error)}</Notice> : callbacks.data?.items.length ? <div className="fact-list">{callbacks.data.items.map((item) => <article className="fact-item" key={item.id}><div><strong>{callbackLabels[item.outcome]}</strong><time>{formatDate(item.occurred_at)}</time></div><p className="fact-item__secondary">{item.actor_name || 'Сотрудник'}{item.comment ? ` · ${item.comment}` : ''}</p></article>)}</div> : <p className="empty-facts">Контрольных звонков пока не было.</p>}<Pager page={callbackPage} total={callbacks.data?.total || 0} change={setCallbackPage} /></div>}</details>}
          <details className="detail-panel detail-disclosure" onToggle={(event) => setHistoryOpen(event.currentTarget.open)}><summary><Clock3 size={17} /> История событий</summary>{historyOpen && <div className="detail-disclosure__body">{history.isPending ? <p className="muted">Загружаем историю…</p> : history.isError ? <Notice tone="error">{errorText(history.error)}</Notice> : history.data?.items.length ? <ol className="history-list">{history.data.items.map((item) => <li key={item.id}><span className="history-dot" /><div><strong>{eventLabel[item.event_type] || item.event_type}</strong><span>{item.actor_name || "Система"} · версия {item.ticket_version}</span>{item.event_type === 'ticket_edited' && <EditedFields data={item.event_data} />}</div><time>{formatDate(item.occurred_at)}</time></li>)}</ol> : <p className="empty-facts">История пуста.</p>}<Pager page={historyPage} total={history.data?.total || 0} change={setHistoryPage} /></div>}</details>
        </div>
        <aside className="ticket-detail-rail ticket-action-rail" aria-label="Управление заявкой">
          <section className="detail-panel ticket-action-panel">
            <header className="ticket-action-panel__header"><h2>Заявка #{ticket.number}</h2><span className="type-label">{typeLabel[ticket.ticket_type]}</span></header>
            <div className="ticket-action-panel__state"><span className={`status-chip status-chip--${ticket.status.toLowerCase()}`}>{statusLabel[ticket.status]}</span><span className={`priority priority--${ticket.priority.toLowerCase()}`}>{priorityLabel[ticket.priority]}</span></div>
            {ticket.status === 'RESOLVED' && <p className="rail-note">{ticket.ticket_type === 'SUBSCRIBER_LINKED' ? 'Ожидает подтверждения абонента. ' : 'Готова к закрытию без обзвона. '}Инженерное решение сохранено{ticket.resolved_at ? ` ${formatDate(ticket.resolved_at)}` : ''}.</p>}
            <dl className="ticket-meta">
              <div><dt>Автор</dt><dd>{ticket.created_by_name || 'Сотрудник'}</dd></div>
              <div><dt>Исполнитель</dt><dd>{ticket.assigned_engineer_id ? assignedName || 'Имя не указано' : 'Не назначен'}</dd></div>
              <div><dt>Обновлена</dt><dd><time dateTime={ticket.updated_at}>{formatDate(ticket.updated_at)}</time></dd></div>
            </dl>
            {(open && (activeWork && (canManualAssign || (canClaim && (ticket.assigned_engineer_id === null || ticket.assigned_engineer_id === session.user.id)) || (canStart && ticket.status === 'OPEN' && ticket.assigned_engineer_id === session.user.id) || (inProgress && (canWriteVerdict || session.permissions.includes('tickets.field_work')))) || (ticket.status === 'RESOLVED' && (ticket.ticket_type === 'SUBSCRIBER_LINKED' ? canCallback : canClose)))) && <div className="ticket-action-panel__actions">
              {activeWork && canClaim && ticket.assigned_engineer_id === null && <Button variant="primary" disabled={!!busy} onClick={() => void runAction('claim', () => ticketApi.claim(id, ticket.version))}>Взять в работу <ArrowRight size={15} /></Button>}
              {activeWork && canClaim && ticket.assigned_engineer_id === session.user.id && <Button variant="secondary" disabled={!!busy} onClick={() => void runAction('release', () => ticketApi.release(id, ticket.version))}>Освободить заявку</Button>}
              {ticket.status === 'OPEN' && canStart && ticket.assigned_engineer_id === session.user.id && <Button variant="primary" disabled={!!busy} onClick={() => void runAction('start', () => ticketApi.start(id, ticket.version))}>Начать работу <ArrowRight size={15} /></Button>}
              {inProgress && canWriteVerdict && <form className="ticket-action-form" onSubmit={submitResolve}><label className="field"><span>Причина решения</span><select aria-label="Причина решения" value={decisionReason} onChange={(event) => setDecisionReason(event.target.value as ResolutionReason)}>{Object.entries(resolutionReasonLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="field"><span>Решение инженера</span><textarea ref={decisionInput} aria-label="Решение инженера" value={decisionText} maxLength={10000} rows={3} onChange={(event) => setDecisionText(event.target.value)} required /></label><Button type="submit" variant="primary" disabled={!decisionText.trim() || !!busy}>Решено</Button></form>}
              {activeWork && canManualAssign && <details className="ticket-action-disclosure"><summary>Назначить инженера</summary>
                {engineers.isPending ? <p className="rail-note">Загружаем инженеров…</p> : engineers.isError ? <Notice tone="error">{errorText(engineers.error)}</Notice> : engineers.data.items.some((item) => item.id !== ticket.assigned_engineer_id) ? <div className="ticket-action-form">
                  <label className="field"><span>Активный инженер</span><select aria-label="Активный инженер" value={engineerId} onChange={(event) => setEngineerId(event.target.value)}><option value="">Выберите инженера</option>{engineers.data.items.filter((item) => item.id !== ticket.assigned_engineer_id).map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
                  <Button disabled={!engineerId || !!busy} onClick={() => void runAction('assign', () => ticketApi.assign(id, engineerId, ticket.version), () => setEngineerId(''))}>Назначить</Button>
                </div> : <p className="rail-note">Нет других активных инженеров.</p>}
              </details>}
              {ticket.status === 'RESOLVED' && ticket.ticket_type === 'SUBSCRIBER_LINKED' && canCallback && <div className="ticket-action-form"><strong>Результат звонка</strong><label className="field"><span>Комментарий (обязателен, если проблема осталась)</span><textarea aria-label="Комментарий обзвона" value={callbackComment} maxLength={2000} rows={2} onChange={(event) => setCallbackComment(event.target.value)} /></label><Button variant="primary" disabled={!!busy} onClick={() => submitCallback('CONFIRMED')}>Проблема устранена</Button><Button disabled={!!busy || !callbackComment.trim()} onClick={() => submitCallback('STILL_BROKEN')}>Проблема осталась</Button><Button variant="quiet" disabled={!!busy} onClick={() => submitCallback('NO_ANSWER')}>Не дозвонилась</Button></div>}
              {ticket.status === 'RESOLVED' && ticket.ticket_type !== 'SUBSCRIBER_LINKED' && canClose && <details className="ticket-action-disclosure"><summary>Закрыть без обзвона</summary><div className="ticket-action-form"><label className="field"><span>Причина закрытия без звонка</span><textarea aria-label="Причина закрытия без звонка" value={closeReason} maxLength={1000} rows={2} onChange={(event) => setCloseReason(event.target.value)} /></label><Button disabled={!!busy || !closeReason.trim()} onClick={() => void runAction('close', () => ticketApi.close(id, ticket.resolved_decision_id!, ticket.version, closeReason.trim()))}>Закрыть заявку</Button></div></details>}
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
