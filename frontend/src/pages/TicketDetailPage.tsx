import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, ClipboardCheck, Clock3, MessageSquareText, RefreshCw, Wrench } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'
import { ApiError, errorText } from '../shared/api/client'
import type { MeResponse } from '../shared/api/types'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { ticketApi } from '../features/tickets/ticket-api'
import { TicketEditor } from '../features/tickets/TicketEditor'
import { settingsApi } from '../features/tickets/settings-api'
import { TemplatePicker } from '../features/tickets/TemplatePicker'
import { MonitoringTicketSection } from '../features/tickets/MonitoringTicketSection'
import { PreviousTickets } from '../features/tickets/PreviousTickets'
import { isDiagnosticReport, MonitoringReport } from '../features/tickets/MonitoringReport'
import { eventLabel, formatDate, priorityLabel, resolutionReasonLabel, statusLabel, ticketTitle, typeLabel } from '../features/tickets/presentation'
import type { CallbackOutcome, ResolutionReason } from '../features/tickets/types'
import { connectionConfiguredIpv4, formatSubscriberAddress } from '../features/tickets/subscriber-presentation'

const callbackLabels: Record<CallbackOutcome, string> = {
  CONFIRMED: 'Проблема устранена', STILL_BROKEN: 'Проблема осталась', NO_ANSWER: 'Не дозвонилась',
}

type TicketTab = 'work' | 'diagnostics' | 'subscriber' | 'history' | 'requests'
const phoneKey = (phone: string) => {
  const digits = phone.replace(/\D/g, '')
  return digits.length >= 5 ? digits : phone.trim().toLowerCase()
}
const ticketTabs: { id: TicketTab; label: string }[] = [
  { id: 'work', label: 'Работа с заявкой' },
  { id: 'diagnostics', label: 'Диагностика' },
  { id: 'subscriber', label: 'Абонент' },
  { id: 'history', label: 'История' },
  { id: 'requests', label: 'Обращения' },
]

function Pager({ page, total, change }: { page: number; total: number; change: (page: number) => void }) {
  if (total <= 20) return null
  return <div className="fact-pager"><span>Страница {page} из {Math.ceil(total / 20)}</span><div><Button variant="quiet" disabled={page === 1} onClick={() => change(page - 1)}>Назад</Button><Button variant="quiet" disabled={page * 20 >= total} onClick={() => change(page + 1)}>Далее</Button></div></div>
}

function EditedFields({ data }: { data: Record<string, unknown> }) {
  const changes = data.changes
  if (!changes || typeof changes !== 'object' || Array.isArray(changes)) return null
  const labels: Record<string, string> = { subject: 'Тема', description: 'Описание', ticket_type: 'Тип', category_id: 'Категория', category_name: 'Название категории', priority: 'Приоритет', caller_full_name: 'Обратившийся', caller_phone: 'Телефон', city: 'Город', address_override: 'Адрес', account_no: 'Лицевой счёт', subscriber_snapshot_id: 'Снимок абонента', connection_snapshot_id: 'Подключение', selected_service_id: 'Услуга', correct_account_no: 'Верный лицевой счёт', erroneous_account_no: 'Ошибочный лицевой счёт', payment_occurred_at: 'Время платежа', payment_amount: 'Сумма платежа' }
  return <div className="history-changes">{Object.entries(changes).filter(([field]) => !!labels[field]).map(([field, entry]) => {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null
    const values = entry as Record<string, unknown>
    const before = typeof values.before === 'string' ? values.before : 'Не указано'
    const after = typeof values.after === 'string' ? values.after : 'Не указано'
    return <p key={field}><b>{labels[field]}:</b> {before} → {after}</p>
  })}{typeof data.reason === 'string' && <p><b>Причина:</b> {data.reason}</p>}</div>
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
  const [tabSelection, setTabSelection] = useState<{ ticketId: string; tab: TicketTab }>({ ticketId: id, tab: 'work' })
  const activeTab = tabSelection.ticketId === id ? tabSelection.tab : 'work'
  const setActiveTab = (tab: TicketTab) => setTabSelection({ ticketId: id, tab })
  const [historyPage, setHistoryPage] = useState(1)
  const [diagnosticPage, setDiagnosticPage] = useState(1)
  const [decisionPage, setDecisionPage] = useState(1)
  const [callbackPage, setCallbackPage] = useState(1)
  const [callbackComment, setCallbackComment] = useState('')
  const [closeReason, setCloseReason] = useState('')
  const callbackRequest = useRef({ id: crypto.randomUUID(), outcome: '' })
  const [comment, setComment] = useState('')
  const [decisionText, setDecisionText] = useState('')
  const [revisingDecision, setRevisingDecision] = useState(false)
  const [decisionReason, setDecisionReason] = useState<ResolutionReason>('NOT_ESTABLISHED')
  const [fieldReason, setFieldReason] = useState('')
  const [visitAddress, setVisitAddress] = useState('')
  const [fieldDirection, setFieldDirection] = useState<'' | 'SUKHUM' | 'OCHAMCHIRA'>('')
  const [editedFieldMessage, setEditedFieldMessage] = useState<{ id: string; text: string } | null>(null)
  const [engineerId, setEngineerId] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [actionError, setActionError] = useState<unknown>(null)
  const [actionSuccess, setActionSuccess] = useState('')
  const [subscriberBusy, setSubscriberBusy] = useState(false)
  const [subscriberError, setSubscriberError] = useState<unknown>(null)
  const [editing, setEditing] = useState(false)
  const editDirty = useRef(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteReason, setDeleteReason] = useState('')
  useEffect(() => { if (confirmDelete) deleteReasonInput.current?.focus() }, [confirmDelete])
  function cancelDelete() {
    setConfirmDelete(false)
    window.requestAnimationFrame(() => deleteTrigger.current?.focus())
  }
  const canAssign = session.permissions.includes('tickets.assign')
  const canStart = session.permissions.includes('tickets.start')
  const canClaim = canStart && session.permissions.includes('tickets.resolve')
  const canReadDiagnostics = session.permissions.includes('diagnostics.read')
  const canDecide = session.permissions.includes('tickets.resolve')
  const canClose = session.permissions.includes('tickets.close')
  const canCallback = session.permissions.includes('tickets.callback')
  const canComment = session.permissions.includes('tickets.comment')
  const canFieldWork = session.permissions.includes('tickets.field_work')

  const ticketQuery = useQuery({ queryKey: ['ticket', id], queryFn: ({ signal }) => ticketApi.get(id, signal), enabled: !!id })
  const related = !!id && ticketQuery.isSuccess
  const resolved = ticketQuery.data?.status === 'RESOLVED'
  const linked = ticketQuery.data?.ticket_type === 'SUBSCRIBER_LINKED'
  const visibleTabs = linked ? ticketTabs : ticketTabs.slice(0, -1)
  useEffect(() => {
    if (ticketQuery.isSuccess && !linked && activeTab === 'requests') setActiveTab('work')
  }, [activeTab, linked, ticketQuery.isSuccess])
  const currentDecisionId = ticketQuery.data?.status === 'CLOSED'
    ? ticketQuery.data.closed_decision_id || ticketQuery.data.resolved_decision_id
    : ticketQuery.data?.resolved_decision_id
  const comments = useQuery({ queryKey: ['ticket-comments', id, commentPage], queryFn: ({ signal }) => ticketApi.comments(id, commentPage, signal), enabled: related })
  const history = useQuery({ queryKey: ['ticket-history', id, historyPage], queryFn: ({ signal }) => ticketApi.history(id, historyPage, signal), enabled: related && activeTab === 'history' })
  const diagnostics = useQuery({ queryKey: ['ticket-diagnostics', id, diagnosticPage], queryFn: ({ signal }) => ticketApi.diagnostics(id, diagnosticPage, signal), enabled: related && canReadDiagnostics && activeTab === 'diagnostics' })
  const decisions = useQuery({ queryKey: ['ticket-decisions', id, decisionPage], queryFn: ({ signal }) => ticketApi.decisions(id, decisionPage, signal), enabled: related && activeTab === 'history' })
  const currentDecision = useQuery({ queryKey: ['ticket-decisions', id, 'current', currentDecisionId], queryFn: ({ signal }) => ticketApi.decision(id, currentDecisionId!, signal), enabled: related && activeTab === 'work' && !!currentDecisionId })
  const reasonChoices = useQuery({ queryKey: ['decision-reasons', 'active'], queryFn: ({ signal }) => settingsApi.reasons(false, signal), enabled: related && canDecide && (resolved || ticketQuery.data?.status === 'IN_PROGRESS') })
  const callbacks = useQuery({ queryKey: ['ticket-callbacks', id, callbackPage], queryFn: ({ signal }) => ticketApi.callbacks(id, callbackPage, signal), enabled: related && linked && (activeTab === 'history' || (resolved && activeTab === 'work')) })
  const engineers = useQuery({ queryKey: ['assignable-engineers'], queryFn: ({ signal }) => ticketApi.engineers(signal), enabled: related && canAssign && ticketQuery.data?.status !== 'CLOSED' && ticketQuery.data?.status !== 'RESOLVED' })
  const fieldHandoff = useQuery({ queryKey: ['ticket-field-work', id], queryFn: ({ signal }) => ticketApi.latestFieldWork(id, signal), enabled: related && canFieldWork })
  const handoffId = fieldHandoff.data?.handoff?.id
  useEffect(() => {
    setVisitAddress(fieldHandoff.data?.handoff?.visit_address ?? '')
    setFieldDirection(fieldHandoff.data?.handoff?.direction ?? '')
  }, [handoffId, fieldHandoff.data?.handoff?.visit_address, fieldHandoff.data?.handoff?.direction])
  const fieldMessages = useQuery({
    queryKey: ['ticket-field-work-messages', id, handoffId],
    queryFn: ({ signal }) => ticketApi.fieldWorkMessages(id, handoffId!, signal),
    enabled: !!handoffId && fieldHandoff.data?.handoff?.delivery_status === 'SENT',
  })

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
        queryClient.invalidateQueries({ queryKey: ['ticket-field-work', id] }),
        queryClient.invalidateQueries({ queryKey: ['ticket-field-work-messages', id] }),
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
  const primaryPhone = ticket.caller_phone || ticket.subscriber?.phone || null
  const hasTicketAddress = !!(ticket.address_override || ticket.subscriber?.actual_address)?.trim()
  const handoff = fieldHandoff.data?.handoff
  const canEditFieldMessage = handoff?.delivery_status === 'DRAFT' || handoff?.delivery_status === 'FAILED'
  const withVisitAddress = (text: string) => !hasTicketAddress && canEditFieldMessage
    ? text.replace(/^Адрес: .*$/m, () => `Адрес: ${visitAddress.trim() || 'не указан'}`) : text
  const originalFieldMessage = withVisitAddress(handoff?.generated_preview ?? handoff?.message_preview ?? '')
  const fieldMessage = editedFieldMessage && editedFieldMessage.id === handoffId
    ? editedFieldMessage.text : withVisitAddress(handoff?.message_preview ?? '')
  const validFieldMessage = !!fieldMessage.trim() && fieldMessage.length <= 4096
    && !Array.from(fieldMessage).some((char) => /[\x00-\x09\x0b-\x1f\x7f]/.test(char))
  const additionalPhones = [
    ticket.subscriber?.phone,
    ...(ticket.subscriber?.contacts?.map((contact) => contact.value) || []),
  ]
    .filter((phone): phone is string => !!phone)
    .filter((phone, index, phones) =>
      phoneKey(phone) !== phoneKey(primaryPhone || '') &&
      phones.findIndex((item) => phoneKey(item) === phoneKey(phone)) === index,
    )
  const canEditAll = session.permissions.includes('tickets.edit.all')
  const canEdit = canEditAll || (ticket.status !== 'CLOSED' && session.permissions.includes('tickets.edit.own') && ticket.created_by === session.user.id)
  const canDelete = session.permissions.includes('tickets.delete.all') || (session.permissions.includes('tickets.delete.own') && ticket.created_by === session.user.id)
  const open = ticket.status !== 'CLOSED'
  const activeWork = ticket.status === 'OPEN' || ticket.status === 'IN_PROGRESS'
  const inProgress = ticket.status === 'IN_PROGRESS'
  const canWriteVerdict = canDecide && (ticket.assigned_engineer_id === session.user.id || session.user.role === 'admin')
  const assignedName = ticket.assigned_engineer_name || engineers.data?.items.find((item) => item.id === ticket.assigned_engineer_id)?.display_name
  const canManualAssign = canAssign

  function leaveEditor(): boolean {
    if (editing && editDirty.current && !window.confirm('Отменить несохранённые изменения заявки?')) return false
    editDirty.current = false
    setEditing(false)
    return true
  }

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
      setRevisingDecision(false)
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
    <div className="page desk-page ticket-detail-page">
      <Link to={listDestination} className="back-link" onClick={(event) => { if (!leaveEditor()) event.preventDefault() }}><ArrowLeft size={15} /> К реестру заявок</Link>
      <header className="ticket-detail-heading">
        <div className="ticket-detail-heading__title"><p className="eyebrow">SERVICEDESK / ЗАЯВКА #{ticket.number}</p><h1>{ticketTitle(ticket.subject)}</h1><div className="ticket-detail-tags"><span className={`status-chip status-chip--${ticket.status.toLowerCase()}`}>{statusLabel[ticket.status]}</span><span className="type-label">{typeLabel[ticket.ticket_type]}</span><span className={`priority priority--${ticket.priority.toLowerCase()}`}>{priorityLabel[ticket.priority]}</span></div></div>
        <div className="user-actions">{canEdit && <Button variant="secondary" onClick={() => { setActiveTab('work'); setEditing(true) }}>Редактировать заявку</Button>}<Button variant="quiet" icon={<RefreshCw size={15} />} onClick={() => { if (leaveEditor()) void ticketQuery.refetch() }} disabled={ticketQuery.isFetching}>Обновить</Button></div>
        <dl className="ticket-detail-facts">
          {ticket.ticket_type === 'ERRONEOUS_PAYMENT' ? <>
            <div><dt>Наименование обратившегося</dt><dd>{ticket.caller_full_name || '—'}</dd></div>
            <div><dt>Верный лицевой счёт</dt><dd>{ticket.correct_account_no || '—'}</dd></div>
            <div><dt>Ошибочный лицевой счёт</dt><dd>{ticket.erroneous_account_no || '—'}</dd></div>
            <div><dt>Дата и время платежа</dt><dd>{ticket.payment_occurred_at ? formatDate(ticket.payment_occurred_at) : '—'}</dd></div>
            <div><dt>Сумма платежа</dt><dd>{ticket.payment_amount ?? '—'}</dd></div>
          </> : <>
          <div><dt>{ticket.ticket_type === 'MANUAL' ? 'Обратившийся' : 'Абонент'}</dt><dd>{ticket.ticket_type === 'MANUAL' ? ticket.caller_full_name || 'Не указан' : ticket.subscriber?.full_name || ticket.subscriber?.organization_name || 'Не привязан'}</dd></div>
          <div><dt>Лицевой счёт</dt><dd>{ticket.account_no || '—'}</dd></div>
          <div><dt>Телефон</dt><dd>{primaryPhone || '—'}</dd></div>
          <div><dt>Город</dt><dd>{ticket.city || '—'}</dd></div>
          <div><dt>Адрес</dt><dd>{ticket.address_override || formatSubscriberAddress(ticket.subscriber?.actual_address, ticket.subscriber?.house_number) || '—'}</dd></div>
          <div><dt>Категория</dt><dd>{ticket.category_name || '—'}</dd></div>
          </>}
        </dl>
      </header>
      {actionError !== null && <Notice tone="error">{actionError instanceof ApiError && actionError.code === 'ticket_already_claimed' ? 'Заявку уже взял другой инженер.' : errorText(actionError)}{actionError instanceof ApiError && actionError.status === 409 ? ' Карточка обновлена; проверьте исполнителя перед повтором.' : ''}</Notice>}
      {actionSuccess && <Notice>{actionSuccess}</Notice>}
      <nav className="ticket-tabs" role="tablist" aria-label="Разделы заявки">
        {visibleTabs.map((tab) => <button key={tab.id} id={`ticket-tab-${tab.id}`} className="ticket-tabs__tab" type="button" role="tab" aria-selected={activeTab === tab.id} aria-controls={`ticket-panel-${tab.id}`} tabIndex={activeTab === tab.id ? 0 : -1} onClick={() => { if (tab.id === 'work' || leaveEditor()) setActiveTab(tab.id) }} onKeyDown={(event) => {
          const index = visibleTabs.findIndex((item) => item.id === activeTab)
          const next = event.key === 'ArrowRight' ? (index + 1) % visibleTabs.length : event.key === 'ArrowLeft' ? (index + visibleTabs.length - 1) % visibleTabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? visibleTabs.length - 1 : -1
          const nextTab = visibleTabs[next]
          if (nextTab) { event.preventDefault(); if (nextTab.id === 'work' || leaveEditor()) { setActiveTab(nextTab.id); document.getElementById(`ticket-tab-${nextTab.id}`)?.focus() } }
        }}>{tab.label}</button>)}
      </nav>
      {activeTab === 'work' && <div id="ticket-panel-work" className="ticket-detail-main" role="tabpanel" aria-labelledby="ticket-tab-work">
        {editing && <section className="detail-panel ticket-work-panel"><div className="detail-panel__heading"><h2>Редактирование заявки</h2></div><TicketEditor key={`${ticket.id}:${ticket.version}`} ticket={ticket} busy={!!busy} basicOnly={!canEditAll} onDirtyChange={(dirty) => { editDirty.current = dirty }} onSave={(payload) => void runAction('edit', () => ticketApi.edit(id, ticket.version, payload), () => { editDirty.current = false; setEditing(false) })} onCancel={() => { editDirty.current = false; setEditing(false) }} /></section>}
        <section className="detail-panel ticket-work-panel" aria-label="Управление заявкой">
          <div className="detail-panel__heading"><h2>Работа с заявкой</h2><span>Автор, исполнитель и текущий этап</span></div>
            <dl className="ticket-meta">
              <div><dt>Автор</dt><dd>{ticket.created_by_name || 'Сотрудник'}</dd></div>
              <div><dt>Исполнитель</dt><dd>{ticket.assigned_engineer_id ? assignedName || 'Имя не указано' : 'Не назначен'}</dd></div>
              <div><dt>Обновлена</dt><dd><time dateTime={ticket.updated_at}>{formatDate(ticket.updated_at)}</time></dd></div>
            </dl>
            {ticket.status === 'RESOLVED' && <p className="rail-note">{ticket.ticket_type === 'SUBSCRIBER_LINKED' ? 'Решение инженера сохранено. Следующий шаг — контрольный звонок.' : 'Решение инженера сохранено. Следующий шаг — закрытие без обзвона.'}</p>}
          <div className="ticket-work-toolbar">
              {activeWork && canClaim && ticket.assigned_engineer_id === null && <Button variant="primary" disabled={!!busy} onClick={() => void runAction('claim', () => ticketApi.claim(id, ticket.version))}>Взять в работу <ArrowRight size={15} /></Button>}
              {activeWork && canAssign && canClaim && session.permissions.includes('tickets.read.all') && ticket.assigned_engineer_id !== null && ticket.assigned_engineer_id !== session.user.id && <Button variant="secondary" disabled={!!busy} onClick={() => void runAction('assign-self', () => ticketApi.assign(id, session.user.id, ticket.version))}>Забрать себе</Button>}
              {activeWork && canClaim && ticket.assigned_engineer_id === session.user.id && <Button variant="secondary" disabled={!!busy} onClick={() => void runAction('release', () => ticketApi.release(id, ticket.version))}>Освободить заявку</Button>}
              {ticket.status === 'OPEN' && canStart && ticket.assigned_engineer_id === session.user.id && <Button variant="primary" disabled={!!busy} onClick={() => void runAction('start', () => ticketApi.start(id, ticket.version))}>Начать работу <ArrowRight size={15} /></Button>}
              {activeWork && canManualAssign && <details className="ticket-action-disclosure"><summary>Назначить инженера</summary>
                {engineers.isPending ? <p className="rail-note">Загружаем инженеров…</p> : engineers.isError ? <Notice tone="error">{errorText(engineers.error)}</Notice> : engineers.data.items.some((item) => item.id !== ticket.assigned_engineer_id) ? <div className="ticket-action-form">
                  <label className="field"><span>Активный инженер</span><select aria-label="Активный инженер" value={engineerId} onChange={(event) => setEngineerId(event.target.value)}><option value="">Выберите инженера</option>{engineers.data.items.filter((item) => item.id !== ticket.assigned_engineer_id).map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label>
                  <Button disabled={!engineerId || !!busy} onClick={() => void runAction('assign', () => ticketApi.assign(id, engineerId, ticket.version), () => setEngineerId(''))}>Назначить</Button>
                </div> : <p className="rail-note">Нет других активных инженеров.</p>}
              </details>}
          </div>
        </section>
        <div className="ticket-work-grid">
          <section className="detail-panel ticket-work-panel"><div className="detail-panel__heading"><h2>Решение инженера</h2></div>
            {currentDecisionId && (currentDecision.isPending ? <p className="muted">Загружаем актуальное решение…</p> : currentDecision.isError ? <Notice tone="error">Не удалось загрузить актуальное решение: {errorText(currentDecision.error)}</Notice> : currentDecision.data && <article className="ticket-current-decision" aria-label="Актуальное решение инженера"><div><strong>{currentDecision.data.reason_name || (currentDecision.data.reason ? resolutionReasonLabel[currentDecision.data.reason] : 'Причина не указана в исторической записи')}</strong><span>{currentDecision.data.engineer_name || ticket.resolved_by_name || 'Инженер'} · <time dateTime={currentDecision.data.created_at}>{formatDate(currentDecision.data.created_at)}</time></span></div><p>{currentDecision.data.decision_text}</p></article>)}
              {canWriteVerdict && reasonChoices.isError && <Notice tone="error">Не удалось загрузить причины решений: {errorText(reasonChoices.error)}</Notice>}
              {canWriteVerdict && reasonChoices.isPending && <p className="muted">Загружаем причины решений…</p>}
              {inProgress && canWriteVerdict && <form className="ticket-action-form" onSubmit={submitResolve}><label className="field"><span>Причина решения</span><select aria-label="Причина решения" disabled={!reasonChoices.isSuccess} value={decisionReason} onChange={(event) => setDecisionReason(event.target.value as ResolutionReason)}>{reasonChoices.data?.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}</select></label><TemplatePicker purpose="decision" maxLength={10000} currentLength={decisionText.length} onInsert={(text) => setDecisionText((current) => current ? `${current}\n${text}` : text)} /><label className="field"><span>Решение инженера</span><textarea ref={decisionInput} aria-label="Решение инженера" value={decisionText} maxLength={10000} rows={3} onChange={(event) => setDecisionText(event.target.value)} required /></label><Button type="submit" variant="primary" disabled={!decisionText.trim() || !!busy || !reasonChoices.isSuccess}>Решено</Button></form>}
            {canWriteVerdict && ticket.status === 'RESOLVED' && <><Button variant="quiet" onClick={() => setRevisingDecision(!revisingDecision)} aria-expanded={revisingDecision}>Уточнить решение</Button>{revisingDecision && <form className="inline-form" onSubmit={submitDecision}><label className="field"><span>Причина решения</span><select aria-label="Причина решения" disabled={!reasonChoices.isSuccess} value={decisionReason} onChange={(event) => setDecisionReason(event.target.value as ResolutionReason)}>{reasonChoices.data?.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}</select></label><TemplatePicker purpose="decision" maxLength={10000} currentLength={decisionText.length} onInsert={(text) => setDecisionText((current) => current ? `${current}\n${text}` : text)} /><label className="field"><span>Новая версия решения</span><textarea aria-label="Новая версия решения" value={decisionText} maxLength={10000} rows={3} onChange={(event) => setDecisionText(event.target.value)} required /></label><Button type="submit" disabled={!decisionText.trim() || !!busy || !reasonChoices.isSuccess}>Сохранить новую версию</Button></form>}</>}
            {ticket.status === 'OPEN' && <p className="empty-facts">Решение доступно после начала работы.</p>}
            {inProgress && !canWriteVerdict && <p className="empty-facts">Решение вводит назначенный инженер.</p>}
            {ticket.status === 'CLOSED' && !currentDecisionId && <p className="empty-facts">Для этой исторической заявки версия решения не сохранена.</p>}
            {ticket.status === 'CLOSED' && <p className="rail-note">Заявка закрыта {ticket.closed_at ? formatDate(ticket.closed_at) : ''}.</p>}
          </section>
              {(inProgress || !!fieldHandoff.data?.handoff) && canFieldWork && <section className="detail-panel ticket-work-panel ticket-work-panel--field"><div className="detail-panel__heading"><h2>Выездные работы</h2></div><div className="ticket-action-form">
                {inProgress && <><label className="field"><span>Причина передачи</span><textarea aria-label="Причина передачи" rows={3} maxLength={2000} value={fieldReason} onChange={(event) => setFieldReason(event.target.value)} /></label>
                <Button disabled={!fieldReason.trim() || !!busy} onClick={() => void runAction('field', () => ticketApi.requestFieldWork(id, fieldReason.trim(), ticket.version), () => setFieldReason(''))}>Зафиксировать запрос</Button></>}
                {fieldHandoff.isPending && <p className="rail-note">Проверяем сохранённую передачу…</p>}
                {fieldHandoff.isError && <Notice tone="error">{errorText(fieldHandoff.error)}</Notice>}
                {fieldHandoff.data?.handoff && <div className="field-dispatch-preview">
                  <strong>Передача в Telegram</strong>
                  {canEditFieldMessage ? <label className="field"><span>Монтажное направление</span><select aria-label="Монтажное направление" value={fieldDirection} onChange={(event) => setFieldDirection(event.target.value as '' | 'SUKHUM' | 'OCHAMCHIRA')} disabled={fieldHandoff.data.handoff.direction != null}><option value="">Выберите группу</option><option value="SUKHUM">Сухум — OSS Sukhum</option><option value="OCHAMCHIRA">Очамчира — OSS Ochamchira</option></select></label> : fieldHandoff.data.handoff.group_name && <p className="rail-note">Группа: {fieldHandoff.data.handoff.group_name}</p>}
                  {canEditFieldMessage ? <><label className="field"><span>Текст сообщения для Telegram</span><textarea aria-label="Текст сообщения для Telegram" rows={8} maxLength={4096} value={fieldMessage} onChange={(event) => setEditedFieldMessage({ id: handoffId!, text: event.target.value })} /><small>{fieldMessage.length} / 4096 символов. Текст сохранится при отправке.</small></label><Button variant="quiet" disabled={!!busy || !originalFieldMessage} onClick={() => setEditedFieldMessage({ id: handoffId!, text: originalFieldMessage })}>Восстановить исходный текст</Button></>
                    : <pre>{fieldHandoff.data.handoff.message_preview || 'Сообщение нельзя сформировать из сохранённого snapshot.'}</pre>}
                  {!hasTicketAddress && canEditFieldMessage && <label className="field"><span>Адрес для выезда (необязательно)</span><input aria-label="Адрес для выезда" maxLength={500} value={visitAddress} onChange={(event) => { const next = event.target.value; setVisitAddress(next); if (editedFieldMessage && editedFieldMessage.id === handoffId) setEditedFieldMessage({ id: handoffId!, text: editedFieldMessage.text.replace(/^Адрес: .*$/m, () => `Адрес: ${next.trim() || 'не указан'}`) }) }} placeholder="Можно отправить без адреса" /><small>Используется только в этой Telegram-передаче; данные NetUP не изменятся.</small></label>}
                  {fieldHandoff.data.handoff.missing_fields.length > 0 && <p className="rail-note">Не хватает данных: {fieldHandoff.data.handoff.missing_fields.join(', ')}.</p>}
                  {fieldHandoff.data.handoff.delivery_status === 'SENT' && <p className="rail-note">Отправлено{fieldHandoff.data.handoff.sent_at ? ` ${formatDate(fieldHandoff.data.handoff.sent_at)}` : ''}. ID сообщения: {fieldHandoff.data.handoff.telegram_message_id}</p>}
                  {fieldHandoff.data.handoff.delivery_status === 'SENT' && <div className="field-work-status" aria-live="polite">
                    <strong>{fieldHandoff.data.handoff.work_status === 'AWAITING_REPORT' ? 'Ожидается результат монтажников' : fieldHandoff.data.handoff.work_status === 'REPORTED' ? 'Монтажники сообщили о выполнении' : 'Результат подтверждён инженером'}</strong>
                    {fieldHandoff.data.handoff.work_status === 'AWAITING_REPORT' && fieldHandoff.data.handoff.completion_prompt_status === 'REQUESTING' && <span>Бот готовит запрос описания работ.</span>}
                    {fieldHandoff.data.handoff.work_status === 'AWAITING_REPORT' && fieldHandoff.data.handoff.completion_prompt_status === 'WAITING' && <span>Бот запросил описание выполненных работ{fieldHandoff.data.handoff.completion_prompt_sent_at ? ` ${formatDate(fieldHandoff.data.handoff.completion_prompt_sent_at)}` : ''}.</span>}
                    {fieldHandoff.data.handoff.work_status === 'AWAITING_REPORT' && fieldHandoff.data.handoff.completion_prompt_status === 'FAILED' && <span>Бот не смог запросить описание. Монтажник может нажать кнопку снова.</span>}
                    {fieldHandoff.data.handoff.work_status === 'AWAITING_REPORT' && fieldHandoff.data.handoff.completion_prompt_status === 'UNCERTAIN' && <span>Доставка запроса описания не подтверждена.</span>}
                    {fieldHandoff.data.handoff.reported_at && <span>Ответ: {formatDate(fieldHandoff.data.handoff.reported_at)}</span>}
                    {fieldHandoff.data.handoff.verified_at && <span>Подтвердил: {fieldHandoff.data.handoff.verified_by_name || 'инженер'}, {formatDate(fieldHandoff.data.handoff.verified_at)}</span>}
                  </div>}
                  {fieldMessages.isPending && fieldHandoff.data.handoff.delivery_status === 'SENT' && <p className="rail-note">Загружаем ответы монтажников…</p>}
                  {fieldMessages.isError && <Notice tone="error">Не удалось загрузить ответы монтажников.</Notice>}
                  {fieldMessages.data?.items.length ? <div className="field-work-replies" aria-label="Ответы монтажников">{fieldMessages.data.items.map((message) => <article key={message.id}>
                    <div><strong>{message.sender_name}</strong><time dateTime={message.telegram_created_at}>{formatDate(message.telegram_created_at)}</time></div>
                    <p>{message.kind === 'COMPLETION_CALLBACK' ? 'Отметил выездные работы выполненными.' : message.body}</p>
                  </article>)}</div> : null}
                  {fieldHandoff.data.handoff.work_status === 'REPORTED' && <Button variant="primary" disabled={!!busy} onClick={() => void runAction('field-verify', () => ticketApi.verifyFieldWork(id, fieldHandoff.data!.handoff!.id))}>Подтвердить результат</Button>}
                  {fieldHandoff.data.handoff.delivery_status === 'SENDING' && <p className="rail-note">Отправка выполняется. Повторный запрос заблокирован.</p>}
                  {fieldHandoff.data.handoff.delivery_status === 'FAILED' && <Notice tone="error">Telegram отклонил отправку. Можно повторить отдельным действием.</Notice>}
                  {fieldHandoff.data.handoff.delivery_status === 'UNCERTAIN' && <Notice tone="error">Результат отправки неизвестен. Автоматический повтор заблокирован во избежание дубля.</Notice>}
                  {fieldHandoff.data.handoff.ready_to_send && <Button variant="primary" disabled={!!busy || !validFieldMessage || !fieldDirection} onClick={() => void runAction('field-send', () => ticketApi.sendFieldWork(id, fieldHandoff.data!.handoff!.id, crypto.randomUUID(), !hasTicketAddress ? visitAddress.trim() || null : undefined, fieldMessage, fieldDirection as 'SUKHUM' | 'OCHAMCHIRA'))}>Отправить боту</Button>}
                </div>}
              </div></section>}
        </div>
        {ticket.status === 'RESOLVED' && <section className="detail-panel ticket-work-panel"><div className="detail-panel__heading"><h2>{ticket.ticket_type === 'SUBSCRIBER_LINKED' ? 'Контрольный звонок' : 'Закрытие заявки'}</h2><span>Следующий шаг после решения инженера</span></div>
          {ticket.ticket_type === 'SUBSCRIBER_LINKED' && additionalPhones.length > 0 && <div className="subscriber-summary">{additionalPhones.map((phone) => <span key={phoneKey(phone)}>Дополнительный контакт: {phone}</span>)}</div>}
              {ticket.status === 'RESOLVED' && ticket.ticket_type === 'SUBSCRIBER_LINKED' && canCallback && <div className="ticket-action-form"><strong>Результат звонка</strong><TemplatePicker purpose="callback" maxLength={2000} currentLength={callbackComment.length} onInsert={(text) => setCallbackComment((current) => current ? `${current}\n${text}` : text)} /><label className="field"><span>Комментарий (обязателен, если проблема осталась)</span><textarea aria-label="Комментарий обзвона" value={callbackComment} maxLength={2000} rows={2} onChange={(event) => setCallbackComment(event.target.value)} /></label><div className="ticket-callback-actions"><Button variant="primary" disabled={!!busy} onClick={() => submitCallback('CONFIRMED')}>Проблема устранена</Button><Button disabled={!!busy || !callbackComment.trim()} onClick={() => submitCallback('STILL_BROKEN')}>Проблема осталась</Button><Button variant="quiet" disabled={!!busy} onClick={() => submitCallback('NO_ANSWER')}>Не дозвонилась</Button></div></div>}
              {ticket.status === 'RESOLVED' && ticket.ticket_type !== 'SUBSCRIBER_LINKED' && canClose && <div className="ticket-action-form"><strong>Закрыть без обзвона</strong><label className="field"><span>Причина закрытия без звонка</span><textarea aria-label="Причина закрытия без звонка" value={closeReason} maxLength={1000} rows={2} onChange={(event) => setCloseReason(event.target.value)} /></label><Button disabled={!!busy || !closeReason.trim()} onClick={() => void runAction('close', () => ticketApi.close(id, ticket.resolved_decision_id!, ticket.version, closeReason.trim()))}>Закрыть заявку</Button></div>}
        </section>}

        <section className="detail-panel"><div className="detail-panel__heading"><h2>Описание</h2><span>Создана {formatDate(ticket.created_at)}</span></div>
            {ticket.description && <p className="ticket-description">{ticket.description}</p>}
          </section>
        {!ticket.subscriber && <div className="unlinked-note">Заявка без привязки к абоненту.</div>}
        {canDelete && <section className="detail-panel ticket-work-panel ticket-work-panel--more">
            <details className="ticket-action-disclosure" onToggle={(event) => { if (!event.currentTarget.open) setConfirmDelete(false) }}><summary>Дополнительно</summary>
              {confirmDelete ? <div className="ticket-action-form" role="group" aria-label="Подтверждение удаления" aria-describedby="ticket-delete-warning" onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); cancelDelete() } }}>
                <p id="ticket-delete-warning" className="rail-note">Удалить заявку №{ticket.number} «{ticketTitle(ticket.subject)}»? Её можно будет восстановить из корзины.</p>
                <label className="field"><span>Причина (необязательно)</span><textarea ref={deleteReasonInput} value={deleteReason} maxLength={500} onChange={(event) => setDeleteReason(event.target.value)} /></label>
                <div className="user-actions"><Button className="ticket-danger-action" disabled={!!busy} onClick={() => void deleteCurrent()}>Подтвердить удаление</Button><Button variant="quiet" disabled={!!busy} onClick={cancelDelete}>Отмена</Button></div>
              </div> : <Button ref={deleteTrigger} className="ticket-danger-action" variant="quiet" onClick={() => setConfirmDelete(true)}>Удалить</Button>}
            </details></section>}
      </div>}
      {activeTab === 'diagnostics' && <div id="ticket-panel-diagnostics" className="ticket-detail-main" role="tabpanel" aria-labelledby="ticket-tab-diagnostics">
        {ticket.selected_connection && <section className="detail-panel"><div className="detail-panel__heading"><h2>Подключение</h2></div><div className="subscriber-summary">
          {ticket.selected_connection.device && <span>Устройство: {ticket.selected_connection.device}</span>}
          {ticket.selected_connection.port && <span>Порт: {ticket.selected_connection.port}</span>}
          {ticket.selected_connection.vlan && <span>VLAN: {ticket.selected_connection.vlan}</span>}
          {connectionConfiguredIpv4(ticket.selected_connection).map((address) => <span key={address}>IPv4 в NetUP: {address}</span>)}
          {ticket.selected_connection.onu_id && <span>ONU: {ticket.selected_connection.onu_id}</span>}
          {ticket.selected_connection.access_type && ticket.selected_connection.access_type !== 'UNKNOWN' && <span>Технология: {ticket.selected_connection.access_type}</span>}
        </div></section>}
        {session.permissions.includes('diagnostics.run') && <MonitoringTicketSection ticket={ticket} />}
          {canReadDiagnostics && <section className="detail-panel"><div className="detail-panel__heading"><h2><Wrench size={17} /> Сохранённая диагностика</h2></div><div>{diagnostics.isPending ? <p className="muted">Загружаем сохранённую диагностику…</p> : diagnostics.isError ? <Notice tone="error">{errorText(diagnostics.error)}</Notice> : diagnostics.data?.items.length ? <div className="fact-list">{diagnostics.data.items.map((item) => <article className="fact-item" key={item.id}><div><strong>{item.source_kind === 'AUTOMATED' ? 'Проверка мониторинга' : 'Историческое наблюдение инженера'}</strong><time>{formatDate(item.observed_at)}</time></div><div>{isDiagnosticReport(item.observation) ? <MonitoringReport report={item.observation} /> : item.observation.finding || 'Результат не указан'}</div></article>)}</div> : <p className="empty-facts">Сохранённых технических записей нет.</p>}<Pager page={diagnosticPage} total={diagnostics.data?.total || 0} change={setDiagnosticPage} /></div></section>}
        {!canReadDiagnostics && !session.permissions.includes('diagnostics.run') && <p className="empty-facts">Диагностика недоступна для вашего профиля.</p>}
      </div>}
      {activeTab === 'subscriber' && <div id="ticket-panel-subscriber" className="ticket-detail-main" role="tabpanel" aria-labelledby="ticket-tab-subscriber">
          {ticket.subscriber && <section className="detail-panel"><div className="detail-panel__heading"><h2>Абонент и подключение</h2><span>{ticket.subscriber.source} · {formatDate(ticket.subscriber.fetched_at)}</span></div><div className="subscriber-summary"><strong>{ticket.subscriber.full_name || ticket.subscriber.organization_name || 'Имя не указано'}</strong><span>ЛС {ticket.account_no}</span>{formatSubscriberAddress(ticket.subscriber.actual_address, ticket.subscriber.house_number) && <span>Адрес: {formatSubscriberAddress(ticket.subscriber.actual_address, ticket.subscriber.house_number)}</span>}{ticket.selected_service_id && <span>Услуга: {ticket.selected_service_id}</span>}{ticket.selected_connection?.device && <span>Устройство: {ticket.selected_connection.device}</span>}{ticket.selected_connection?.port && <span>Порт: {ticket.selected_connection.port}</span>}{ticket.selected_connection?.vlan && <span>VLAN: {ticket.selected_connection.vlan}</span>}{connectionConfiguredIpv4(ticket.selected_connection).map((address) => <span key={address}>IPv4 в NetUP: {address}</span>)}{ticket.selected_connection?.access_type && ticket.selected_connection.access_type !== 'UNKNOWN' && <span>Технология: {ticket.selected_connection.access_type}</span>}{ticket.selected_connection?.onu_id && <span>ONU: {ticket.selected_connection.onu_id}</span>}{ticket.account_no && session.permissions.includes('subscribers.read') && <Button variant="secondary" disabled={subscriberBusy} onClick={() => void openSubscriber(ticket.account_no!)}>Открыть карточку абонента <ArrowRight size={15} /></Button>}{subscriberError !== null && <Notice tone="error">{errorText(subscriberError)}</Notice>}</div></section>}
        {!ticket.subscriber && <div className="unlinked-note">Заявка без привязки к абоненту.</div>}
      </div>}
      {activeTab === 'history' && <div id="ticket-panel-history" className="ticket-detail-main" role="tabpanel" aria-labelledby="ticket-tab-history">
          <section className="detail-panel"><div className="detail-panel__heading"><h2><MessageSquareText size={17} /> Комментарии</h2><span>{comments.data?.total ?? '—'} записей</span></div>{comments.isPending ? <p className="muted">Загружаем комментарии…</p> : comments.isError ? <Notice tone="error">{errorText(comments.error)}</Notice> : comments.data.items.length ? <div className="fact-list">{comments.data.items.map((item) => <article className="fact-item" key={item.id}><div><strong>{item.author_name || "Сотрудник"}</strong><time>{formatDate(item.created_at)}</time></div><p>{item.body}</p></article>)}</div> : <p className="empty-facts">Комментариев пока нет.</p>}<Pager page={commentPage} total={comments.data?.total || 0} change={setCommentPage} />{canComment && open && <form className="inline-form" onSubmit={submitComment}><TemplatePicker purpose="comment" maxLength={10000} currentLength={comment.length} onInsert={(text) => setComment((current) => current ? `${current}\n${text}` : text)} /><label className="field"><span>Добавить сведения</span><textarea aria-label="Новый комментарий" value={comment} maxLength={10000} rows={3} onChange={(event) => setComment(event.target.value)} /></label><Button type="submit" disabled={!comment.trim() || !!busy}>Сохранить комментарий</Button></form>}</section>
          <section className="detail-panel"><div className="detail-panel__heading"><h2><ClipboardCheck size={17} /> Решения инженера</h2></div><div>{decisions.isPending ? <p className="muted">Загружаем решения…</p> : decisions.isError ? <Notice tone="error">{errorText(decisions.error)}</Notice> : decisions.data?.items.length ? <div className="fact-list">{decisions.data.items.map((item) => <article className="fact-item" key={item.id}><div><strong>{item.id === ticket.resolved_decision_id ? 'Текущий вердикт' : 'Решение инженера'}</strong><time>{formatDate(item.created_at)}</time></div><p>{item.decision_text}</p><p className="fact-item__secondary">{item.engineer_name || 'Инженер'} · {item.reason_name || (item.reason ? resolutionReasonLabel[item.reason] : 'Причина не указана в исторической записи')}</p>{item.performed_actions && <p className="fact-item__secondary">Ранее указанные действия: {item.performed_actions}</p>}</article>)}</div> : <p className="empty-facts">Решение ещё не зафиксировано.</p>}<Pager page={decisionPage} total={decisions.data?.total || 0} change={setDecisionPage} /></div></section>
          {ticket.ticket_type === 'SUBSCRIBER_LINKED' && <section className="detail-panel"><div className="detail-panel__heading"><h2>История контрольных обзвонов</h2></div><div>{callbacks.isPending ? <p className="muted">Загружаем звонки…</p> : callbacks.isError ? <Notice tone="error">{errorText(callbacks.error)}</Notice> : callbacks.data?.items.length ? <div className="fact-list">{callbacks.data.items.map((item) => <article className="fact-item" key={item.id}><div><strong>{callbackLabels[item.outcome]}</strong><time>{formatDate(item.occurred_at)}</time></div><p className="fact-item__secondary">{item.actor_name || 'Сотрудник'}{item.comment ? ` · ${item.comment}` : ''}</p></article>)}</div> : <p className="empty-facts">Контрольных звонков пока не было.</p>}<Pager page={callbackPage} total={callbacks.data?.total || 0} change={setCallbackPage} /></div></section>}
          <section className="detail-panel"><div className="detail-panel__heading"><h2><Clock3 size={17} /> История событий</h2></div><div>{history.isPending ? <p className="muted">Загружаем историю…</p> : history.isError ? <Notice tone="error">{errorText(history.error)}</Notice> : history.data?.items.length ? <ol className="history-list">{history.data.items.map((item) => <li key={item.id}><span className="history-dot" /><div><strong>{eventLabel[item.event_type] || item.event_type}</strong><span>{item.actor_name || "Система"} · версия {item.ticket_version}</span>{item.event_type === 'ticket_edited' && <EditedFields data={item.event_data} />}</div><time>{formatDate(item.occurred_at)}</time></li>)}</ol> : <p className="empty-facts">История пуста.</p>}<Pager page={historyPage} total={history.data?.total || 0} change={setHistoryPage} /></div></section>
      </div>}
      {activeTab === 'requests' && linked && <div id="ticket-panel-requests" className="ticket-detail-main" role="tabpanel" aria-labelledby="ticket-tab-requests">
        {ticket.subscriber_snapshot_id
          ? <PreviousTickets key={`${id}:${ticket.subscriber_snapshot_id}`} ticketId={id} snapshotId={ticket.subscriber_snapshot_id} />
          : <p className="empty-facts">Нет привязки к абоненту.</p>}
      </div>}
    </div>
  )
}
