import { useEffect, useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import { priorityLabel, typeLabel } from './presentation'
import { ticketApi } from './ticket-api'
import type { EditTicketInput, SubscriberCard, TicketDetail, TicketPriority, TicketType } from './types'

type Draft = {
  ticket_type: TicketType; subject: string; description: string; category_id: string
  priority: TicketPriority; caller_full_name: string; caller_phone: string; city: string
  address_override: string; correct_account_no: string; erroneous_account_no: string
  payment_occurred_at: string; payment_amount: string
}

function initial(ticket: TicketDetail): Draft {
  const paymentInstant = ticket.payment_occurred_at ? new Date(ticket.payment_occurred_at) : null
  const localPaymentInstant = paymentInstant
    ? new Date(paymentInstant.getTime() - paymentInstant.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
    : ''
  return {
    ticket_type: ticket.ticket_type, subject: ticket.subject || '', description: ticket.description,
    category_id: ticket.category_id || '', priority: ticket.priority,
    caller_full_name: ticket.caller_full_name || '', caller_phone: ticket.caller_phone || '',
    city: ticket.city || '', address_override: ticket.address_override || '',
    correct_account_no: ticket.correct_account_no || '', erroneous_account_no: ticket.erroneous_account_no || '',
    payment_occurred_at: localPaymentInstant,
    payment_amount: ticket.payment_amount || '',
  }
}

export function TicketEditor({ ticket, busy, basicOnly, onSave, onCancel, onDirtyChange }: {
  ticket: TicketDetail; busy: boolean; basicOnly?: boolean; onSave: (payload: EditTicketInput) => void; onCancel: () => void; onDirtyChange?: (dirty: boolean) => void
}) {
  const baseline = initial(ticket)
  const [draft, setDraft] = useState<Draft>(() => baseline)
  const [accountNo, setAccountNo] = useState(ticket.account_no || '')
  const [lookup, setLookup] = useState<SubscriberCard | null>(null)
  const [lookupError, setLookupError] = useState<unknown>(null)
  const [lookupBusy, setLookupBusy] = useState(false)
  const [serviceId, setServiceId] = useState('')
  const [connectionId, setConnectionId] = useState('')
  const [reason, setReason] = useState('')
  const categories = useQuery({ queryKey: ['ticket-categories'], queryFn: ({ signal }) => ticketApi.categories(signal), enabled: !basicOnly && ticket.ticket_type !== 'ERRONEOUS_PAYMENT' })
  const changed = JSON.stringify(draft) !== JSON.stringify(baseline) || !!lookup
  const sourceChanged = !!lookup || draft.ticket_type !== ticket.ticket_type
  const reasonRequired = sourceChanged || ticket.status === 'CLOSED'
  const selectedCategory = categories.data?.find((item) => item.id === draft.category_id)
  const services = lookup?.services.filter((item) => !item.account_id || item.account_id === lookup.subscriber.billing_account) || []
  const canChangeSource = ticket.status === 'OPEN' || ticket.status === 'IN_PROGRESS'
  const unconfirmedAccount = draft.ticket_type === 'SUBSCRIBER_LINKED' && accountNo.trim() !== (ticket.account_no || '') && !lookup
  const canChangeType = ticket.status === 'OPEN' && !ticket.assigned_engineer_id && ticket.ticket_type !== 'ERRONEOUS_PAYMENT'

  useEffect(() => {
    if (!changed) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [changed])
  useEffect(() => { onDirtyChange?.(changed) }, [changed, onDirtyChange])

  function update<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  async function findAccount() {
    const value = accountNo.trim()
    if (!value || lookupBusy) return
    setLookupBusy(true)
    setLookup(null)
    setLookupError(null)
    try {
      const result = await ticketApi.lookup(value)
      if (result.subscriber.billing_account !== value) throw new Error('Лицевой счёт не совпал с запросом')
      setLookup(result)
      const eligible = result.services.filter((item) => !item.account_id || item.account_id === value)
      setServiceId(eligible.length === 1 ? eligible[0]!.service_id : '')
      setConnectionId(result.connections.length === 1 ? result.connections[0]!.connection_snapshot_id : '')
    } catch (error) { setLookupError(error) }
    finally { setLookupBusy(false) }
  }

  function cancel() {
    if (!changed || window.confirm('Отменить несохранённые изменения?')) onCancel()
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !changed) return
    const payload: EditTicketInput = {}
    const textFields = ['subject', 'description', 'caller_full_name', 'caller_phone', 'city', 'address_override', 'correct_account_no', 'erroneous_account_no'] as const
    for (const key of textFields) {
      if (draft[key] !== baseline[key]) {
        if (key === 'description') payload.description = draft.description.trim()
        else payload[key] = draft[key].trim() || null
      }
    }
    if (draft.ticket_type !== baseline.ticket_type) payload.ticket_type = draft.ticket_type
    if (draft.category_id !== baseline.category_id) payload.category_id = draft.category_id || null
    if (draft.priority !== baseline.priority) payload.priority = draft.priority
    if (draft.payment_occurred_at !== baseline.payment_occurred_at) payload.payment_occurred_at = draft.payment_occurred_at ? new Date(draft.payment_occurred_at).toISOString() : null
    if (draft.payment_amount !== baseline.payment_amount) payload.payment_amount = draft.payment_amount || null
    if (draft.ticket_type === 'SUBSCRIBER_LINKED' && lookup) {
      payload.lookup_id = lookup.lookup_id
      if (serviceId) payload.selected_service_id = serviceId
      if (connectionId) payload.connection_snapshot_id = connectionId
    }
    if (draft.ticket_type === 'SUBSCRIBER_LINKED' && draft.ticket_type !== ticket.ticket_type) payload.caller_full_name = null
    if (draft.ticket_type === 'INTERNAL' && draft.ticket_type !== ticket.ticket_type) payload.caller_full_name = null
    if (reasonRequired) payload.reason = reason.trim()
    onSave(payload)
  }

  if (basicOnly) return <form className="ticket-editor" onSubmit={submit} aria-label="Редактирование заявки">
    {ticket.ticket_type !== 'ERRONEOUS_PAYMENT' && <label className="field"><span>Тема</span><input value={draft.subject} maxLength={160} onChange={(event) => update('subject', event.target.value)} /></label>}
    <label className="field"><span>Описание проблемы</span><textarea value={draft.description} maxLength={10000} rows={4} required={ticket.ticket_type !== 'MANUAL' && ticket.ticket_type !== 'ERRONEOUS_PAYMENT'} onChange={(event) => update('description', event.target.value)} /></label>
    <div className="user-actions"><Button type="submit" disabled={busy || !changed}>Сохранить изменения</Button><Button type="button" variant="quiet" onClick={cancel}>Отмена</Button></div>
  </form>

  return <form className="ticket-editor" onSubmit={submit} aria-label="Редактирование заявки">
    {categories.isError && <Notice tone="error">Не удалось загрузить категории: {errorText(categories.error)}</Notice>}
    <div className="ticket-editor__grid">
      <fieldset className="ticket-editor__group"><legend>Обращение</legend>
        <label className="field"><span>Тип заявки</span><select value={draft.ticket_type} disabled={!canChangeType} onChange={(event) => {
          update('ticket_type', event.target.value as TicketType); setLookup(null)
        }}>{(['MANUAL', 'SUBSCRIBER_LINKED', 'INTERNAL'] as const).map((kind) => <option key={kind} value={kind}>{typeLabel[kind]}</option>)}{ticket.ticket_type === 'ERRONEOUS_PAYMENT' && <option value="ERRONEOUS_PAYMENT">{typeLabel.ERRONEOUS_PAYMENT}</option>}</select></label>
        {draft.ticket_type !== 'ERRONEOUS_PAYMENT' && <><label className="field"><span>Категория</span><select value={draft.category_id} onChange={(event) => {
          const id = event.target.value; setDraft((current) => ({ ...current, category_id: id, subject: categories.data?.find((item) => item.id === id && !item.is_other)?.name || current.subject }))
        }}><option value="">Своя тема / без категории</option>{categories.data?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label className="field"><span>Тема</span><input value={draft.subject} maxLength={160} disabled={!!selectedCategory && !selectedCategory.is_other} onChange={(event) => update('subject', event.target.value)} required={draft.ticket_type === 'MANUAL'} /></label></>}
        <label className="field"><span>{draft.ticket_type === 'ERRONEOUS_PAYMENT' ? 'Комментарий' : 'Описание проблемы'}</span><textarea value={draft.description} maxLength={10000} rows={4} required={draft.ticket_type !== 'MANUAL' && draft.ticket_type !== 'ERRONEOUS_PAYMENT'} onChange={(event) => update('description', event.target.value)} /></label>
        <label className="field"><span>Приоритет</span><select value={draft.priority} onChange={(event) => update('priority', event.target.value as TicketPriority)}>{(['LOW', 'NORMAL', 'HIGH', 'URGENT'] as const).map((value) => <option key={value} value={value}>{priorityLabel[value]}</option>)}</select></label>
      </fieldset>
      <fieldset className="ticket-editor__group"><legend>Контакт и адрес</legend>
        {(draft.ticket_type === 'MANUAL' || draft.ticket_type === 'ERRONEOUS_PAYMENT') && <label className="field"><span>Имя обратившегося</span><input value={draft.caller_full_name} maxLength={160} required onChange={(event) => update('caller_full_name', event.target.value)} /></label>}
        {draft.ticket_type !== 'ERRONEOUS_PAYMENT' && <><label className="field"><span>Контактный телефон</span><input value={draft.caller_phone} maxLength={50} required={draft.ticket_type === 'MANUAL'} onChange={(event) => update('caller_phone', event.target.value)} /></label><label className="field"><span>Город</span><input value={draft.city} maxLength={160} required={draft.ticket_type === 'MANUAL'} onChange={(event) => update('city', event.target.value)} /></label><label className="field"><span>Адрес для заявки</span><input value={draft.address_override} maxLength={300} placeholder={ticket.subscriber?.actual_address || 'Не указан'} onChange={(event) => update('address_override', event.target.value)} /></label></>}
        {draft.ticket_type === 'ERRONEOUS_PAYMENT' && <><label className="field"><span>Верный лицевой счёт</span><input value={draft.correct_account_no} maxLength={100} required onChange={(event) => update('correct_account_no', event.target.value)} /></label><label className="field"><span>Ошибочный лицевой счёт</span><input value={draft.erroneous_account_no} maxLength={100} required onChange={(event) => update('erroneous_account_no', event.target.value)} /></label><label className="field"><span>Дата и время платежа</span><input type="datetime-local" value={draft.payment_occurred_at} required onChange={(event) => update('payment_occurred_at', event.target.value)} /></label><label className="field"><span>Сумма платежа</span><input type="number" min="0.01" step="0.01" value={draft.payment_amount} required onChange={(event) => update('payment_amount', event.target.value)} /></label></>}
      </fieldset>
    </div>
    {canChangeSource && draft.ticket_type === 'SUBSCRIBER_LINKED' && <fieldset className="ticket-editor__group"><legend>Привязка к NetUP</legend><p className="rail-note">Текущая привязка: {ticket.account_no || 'нет'}. Перепривязка меняет только текущие данные заявки; прежние события и снимки остаются в истории. Требуется подтверждённый поиск по лицевому счёту.</p><div className="ticket-editor__lookup"><label className="field"><span>Лицевой счёт</span><input value={accountNo} onChange={(event) => { setAccountNo(event.target.value); setLookup(null) }} /></label><Button type="button" variant="secondary" disabled={lookupBusy || !accountNo.trim()} onClick={() => void findAccount()}>Найти в NetUP</Button></div>{lookupError !== null && <Notice tone="error">{errorText(lookupError)}</Notice>}{lookup && <><p>Подтверждён аккаунт {lookup.subscriber.billing_account}: {lookup.subscriber.full_name || lookup.subscriber.organization_name || 'имя не указано'}</p><label className="field"><span>Услуга</span><select value={serviceId} required={services.length > 1} onChange={(event) => setServiceId(event.target.value)}><option value="">{services.length ? 'Выберите услугу' : 'Нет услуг'}</option>{services.map((item) => <option key={item.service_id} value={item.service_id}>{item.name || item.service_id}</option>)}</select></label><label className="field"><span>Подключение</span><select value={connectionId} required={lookup.connections.length > 1} onChange={(event) => setConnectionId(event.target.value)}><option value="">{lookup.connections.length ? 'Выберите подключение' : 'Нет подключений'}</option>{lookup.connections.map((item) => <option key={item.connection_snapshot_id} value={item.connection_snapshot_id}>{item.device || 'Устройство не указано'} · {item.port || 'порт не указан'}</option>)}</select></label></>}</fieldset>}
    {reasonRequired && <label className="field"><span>Причина корректировки</span><textarea value={reason} maxLength={500} required rows={2} onChange={(event) => setReason(event.target.value)} /></label>}
    {unconfirmedAccount && <p className="rail-note">Подтвердите новый лицевой счёт поиском в NetUP.</p>}
    <div className="user-actions"><Button type="submit" disabled={busy || !changed || unconfirmedAccount || (draft.ticket_type === 'SUBSCRIBER_LINKED' && ticket.ticket_type !== 'SUBSCRIBER_LINKED' && !lookup) || (reasonRequired && !reason.trim())}>Сохранить изменения</Button><Button type="button" variant="quiet" onClick={cancel}>Отмена</Button></div>
  </form>
}
