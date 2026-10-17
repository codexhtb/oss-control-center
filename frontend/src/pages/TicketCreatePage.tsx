import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, FilePlus2, Layers3, Search, UserRound } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import type { MeResponse } from '../shared/api/types'
import { ApiError, errorText } from '../shared/api/client'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { ticketApi } from '../features/tickets/ticket-api'
import { priorityLabel, statusLabel, ticketTitle } from '../features/tickets/presentation'
import type { CreateTicketInput, SubscriberCard, TicketPriority, TicketType } from '../features/tickets/types'
import { formatSubscriberAddress } from '../features/tickets/subscriber-presentation'
import { formatDate } from '../features/tickets/presentation'

const priorities: TicketPriority[] = ['LOW', 'NORMAL', 'HIGH', 'URGENT']

export function TicketCreatePage({ session }: { session: MeResponse }) {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const linkedLookupId = searchParams.get('lookup')
  const queryClient = useQueryClient()
  const [type, setType] = useState<TicketType>('SUBSCRIBER_LINKED')
  const [subject, setSubject] = useState('')
  const [description, setDescription] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [phoneChoice, setPhoneChoice] = useState('')
  const [manualPhone, setManualPhone] = useState('')
  const [priority, setPriority] = useState<TicketPriority>('NORMAL')
  const [accountNo, setAccountNo] = useState('')
  const [card, setCard] = useState<SubscriberCard | null>(null)
  const currentAccount = useRef('')
  const [serviceId, setServiceId] = useState('')
  const [connectionId, setConnectionId] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const [activePage, setActivePage] = useState(1)
  const [otherProblem, setOtherProblem] = useState(false)
  const [duplicateReason, setDuplicateReason] = useState('')
  const existingLookup = useQuery({
    queryKey: ['subscriber', linkedLookupId],
    queryFn: ({ signal }) => ticketApi.getLookup(linkedLookupId!, signal),
    enabled: !!linkedLookupId,
  })
  useEffect(() => {
    if (!existingLookup.data) return
    const result = existingLookup.data
    setType('SUBSCRIBER_LINKED')
    setAccountNo(result.subscriber.billing_account)
    currentAccount.current = result.subscriber.billing_account
    setCard(result)
    setPhoneChoice('')
    setManualPhone('')
    setOtherProblem(false)
    setDuplicateReason('')
    setActivePage(1)
    const eligible = result.services.filter((service) => !service.account_id || service.account_id === result.subscriber.billing_account)
    setServiceId(eligible.length === 1 ? eligible[0]!.service_id : '')
    setConnectionId(result.connections.length === 1 ? result.connections[0]!.connection_snapshot_id : '')
  }, [existingLookup.data])
  const lookup = useMutation({
    mutationFn: (account: string) => ticketApi.lookup(account),
    onSuccess: (result) => {
      if (result.subscriber.billing_account !== currentAccount.current) return
      setCard(result)
      setPhoneChoice('')
      setManualPhone('')
      setOtherProblem(false)
      setDuplicateReason('')
      setActivePage(1)
      const eligible = result.services.filter((service) => !service.account_id || service.account_id === result.subscriber.billing_account)
      setServiceId(eligible.length === 1 ? eligible[0]!.service_id : '')
      setConnectionId(result.connections.length === 1 ? result.connections[0]!.connection_snapshot_id : '')
      edited()
    },
  })
  const mutation = useMutation({
    mutationFn: (payload: CreateTicketInput) => ticketApi.create(payload),
    onSuccess: (ticket) => {
      void queryClient.invalidateQueries({ queryKey: ['tickets'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      navigate(`/tickets/${ticket.id}`)
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === 'active_ticket_exists') {
        void queryClient.invalidateQueries({ queryKey: ['active-subscriber-tickets'] })
      }
    },
  })
  const canCreate = session.permissions.includes('tickets.create')
  const linked = type === 'SUBSCRIBER_LINKED'
  const categories = useQuery({ queryKey: ['ticket-categories'], queryFn: ({ signal }) => ticketApi.categories(signal), enabled: canCreate })
  const selectedCategory = categories.data?.find((item) => item.id === categoryId && item.is_active)
  const phoneChoices = [...new Set([card?.subscriber.phone, ...(card?.subscriber.contacts.map((contact) => contact.value) ?? [])].filter((value): value is string => !!value?.trim()))]
  const callerPhone = phoneChoice === '__manual__' ? manualPhone.trim() : phoneChoice.startsWith('number:') ? phoneChoices[Number(phoneChoice.slice(7))] : undefined
  const active = useQuery({
    queryKey: ['active-subscriber-tickets', card?.lookup_id, activePage],
    queryFn: ({ signal }) => ticketApi.activeSubscriberTickets(card!.lookup_id, activePage, signal),
    enabled: linked && !!card,
  })
  const hasActive = !!active.data && (active.data.total_visible > 0 || active.data.has_hidden_active)
  const eligibleServices = card?.services.filter((service) => !service.account_id || service.account_id === card.subscriber.billing_account) ?? []
  const ambiguousService = eligibleServices.length > 1
  const ambiguousConnection = (card?.connections.length ?? 0) > 1
  const expired = card ? card.is_expired || Date.now() >= new Date(card.expires_at).getTime() : false
  const canSubmit = (!linked ? !!description.trim() : !!selectedCategory && (!selectedCategory.is_other || !!subject.trim()) && (!callerPhone || callerPhone.length <= 50) && !!card && !expired && active.isSuccess && (!hasActive || (otherProblem && !!duplicateReason.trim())) && (card.services.length === 0 || eligibleServices.length > 0) && (eligibleServices.length === 0 || !!serviceId) && (!ambiguousConnection || !!connectionId))

  function edited() {
    setRequestId(crypto.randomUUID())
    mutation.reset()
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canCreate || mutation.isPending || !canSubmit) return
    const common = {
      subject: (linked && !selectedCategory?.is_other ? undefined : subject.trim() || undefined),
      description: description.trim() || undefined,
      priority,
      client_request_id: requestId,
    }
    const payload: CreateTicketInput = type === 'SUBSCRIBER_LINKED' && card
      ? { ...common, ticket_type: type, lookup_id: card.lookup_id,
          category_id: categoryId,
          ...(callerPhone ? { caller_phone: callerPhone } : {}),
          ...(serviceId ? { selected_service_id: serviceId } : {}),
          ...(connectionId ? { connection_snapshot_id: connectionId } : {}),
          ...(hasActive && otherProblem ? { duplicate_reason: duplicateReason.trim() } : {}) }
      : { ...common, ticket_type: type as 'MANUAL' | 'INTERNAL' }
    mutation.mutate(payload)
  }

  function performLookup() {
    if (!accountNo.trim() || lookup.isPending) return
    currentAccount.current = accountNo.trim()
    setCard(null)
    setPhoneChoice('')
    setManualPhone('')
    setOtherProblem(false)
    setDuplicateReason('')
    setActivePage(1)
    lookup.reset()
    lookup.mutate(currentAccount.current)
  }

  if (!canCreate) return <Notice tone="error">У вашей роли нет права создавать заявки.</Notice>
  return (
    <div className="page desk-page">
      <Link to="/tickets" className="back-link"><ArrowLeft size={15} /> К реестру заявок</Link>
      <div className="page-heading desk-heading"><div><p className="eyebrow">SERVICEDESK / СОЗДАНИЕ</p><h1>Новая заявка</h1><p className="page-heading__description">Найдите абонента по лицевому счёту и выберите тему обращения.</p></div></div>
      <div className="create-layout">
        <form className="create-card" onSubmit={submit}>
          <div className="form-section-heading"><span className="step-number">01</span><div><h2>Тип заявки</h2><p>Общий workflow для обращений и внутренних задач.</p></div></div>
          <div className="type-picker" role="group" aria-label="Тип заявки">
            <button type="button" className={`type-option ${type === 'MANUAL' ? 'type-option--selected' : ''}`} onClick={() => { setType('MANUAL'); edited() }}><FilePlus2 size={20} /><strong>Ручная</strong><span>Обращение без привязки к NetUP</span></button>
            <button type="button" className={`type-option ${type === 'INTERNAL' ? 'type-option--selected' : ''}`} onClick={() => { setType('INTERNAL'); edited() }}><Layers3 size={20} /><strong>Внутренняя</strong><span>Задача команды OSS</span></button>
            <button type="button" className={`type-option ${linked ? 'type-option--selected' : ''}`} onClick={() => { setType('SUBSCRIBER_LINKED'); edited() }}><UserRound size={20} /><strong>По абоненту</strong><span>Из Subscriber 360</span></button>
          </div>
          {linked && <div className="lookup-section">
            {existingLookup.isError && <Notice tone="error">Не удалось открыть сохранённую карточку. {errorText(existingLookup.error)}</Notice>}
            <div className="form-section-heading"><span className="step-number">02</span><div><h2>Поиск абонента</h2><p>Введите лицевой счёт. Данные берутся из согласованного источника.</p></div></div>
            <div className="lookup-search"><label className="field"><span>Лицевой счёт</span><input id="billing-account" name="billing-account" value={accountNo} maxLength={100} placeholder="Номер ЛС" onChange={(event) => { setAccountNo(event.target.value); currentAccount.current = event.target.value.trim(); setCard(null); setOtherProblem(false); setDuplicateReason(''); setActivePage(1); lookup.reset(); edited() }} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); performLookup() } }} /></label><Button type="button" icon={<Search size={16} />} onClick={performLookup} disabled={!accountNo.trim() || lookup.isPending}>{lookup.isPending ? 'Ищем…' : 'Найти'}</Button></div>
            {lookup.isError && <Notice tone="error">{errorText(lookup.error)} Данные абонента не подтверждены; заявка по абоненту не создана.</Notice>}
            {card && <div className="lookup-card"><div className="lookup-card__top"><div><span className="eyebrow">ПОДТВЕРЖДЁННАЯ КАРТОЧКА</span><h3>{card.subscriber.full_name || card.subscriber.organization_name || 'Имя неизвестно'}</h3><span>ЛС {card.subscriber.billing_account}</span></div><span className="source-chip">{card.source} · {formatDate(card.fetched_at)}</span></div>
              <div className="lookup-facts"><span>Контакт: <strong>{card.subscriber.phone || 'UNKNOWN'}</strong></span><span>Адрес: <strong>{formatSubscriberAddress(card.subscriber.actual_address, card.subscriber.house_number) || 'UNKNOWN'}</strong></span></div>
              {ambiguousService && <label className="field"><span>Услуга <em>*</em></span><select aria-label="Услуга" value={serviceId} onChange={(event) => { setServiceId(event.target.value); setConnectionId(''); edited() }}><option value="">Выберите услугу</option>{eligibleServices.map((service) => <option key={service.service_id} value={service.service_id}>{service.name || service.service_id} · {service.service_id}</option>)}</select></label>}
              {eligibleServices.length === 1 && <p className="lookup-single">Услуга: {eligibleServices[0]!.name || eligibleServices[0]!.service_id}</p>}
              {eligibleServices.length === 0 && card.services.length > 0 && <Notice tone="error">Услуги относятся к другому ЛС. Найдите нужный лицевой счёт для связанной заявки.</Notice>}
              {ambiguousConnection && <label className="field"><span>Подключение <em>*</em></span><select aria-label="Подключение" value={connectionId} onChange={(event) => { setConnectionId(event.target.value); edited() }}><option value="">Выберите подключение</option>{card.connections.map((connection) => <option key={connection.connection_snapshot_id} value={connection.connection_snapshot_id} disabled={!!(serviceId && connection.service_id && connection.service_id !== serviceId)}>{connection.access_type} · {connection.device || 'Устройство UNKNOWN'} · {connection.port || 'Порт UNKNOWN'}{connection.service_id ? ` · ${connection.service_id}` : ''}</option>)}</select></label>}
              {card.connections.length === 1 && <p className="lookup-single">Подключение: {card.connections[0]!.access_type} · {card.connections[0]!.device || 'Устройство UNKNOWN'} · {card.connections[0]!.port || 'Порт UNKNOWN'}</p>}
              {card.connections.length === 0 && <p className="lookup-single">Подключение в источнике не указано. Техническое состояние остаётся UNKNOWN.</p>}
              <label className="field"><span>Телефон входящего звонка <small>необязательно</small></span><select aria-label="Телефон входящего звонка" value={phoneChoice} onChange={(event) => { setPhoneChoice(event.target.value); edited() }}><option value="">Не указан</option>{phoneChoices.map((phone, index) => <option key={`${phone}-${index}`} value={`number:${index}`}>{phone} · контакт NetUP</option>)}<option value="__manual__">Другой номер</option></select></label>
              {phoneChoice === '__manual__' && <label className="field"><span>Другой номер</span><input type="tel" value={manualPhone} maxLength={50} placeholder="Введите номер, сообщённый при звонке" onChange={(event) => { setManualPhone(event.target.value); edited() }} /></label>}
              {expired && <Notice tone="error">Карточка устарела для создания заявки. Выполните поиск повторно.</Notice>}
            </div>}
            {card && (active.isPending ? <p role="status" className="muted">Проверяем открытые обращения…</p> : active.isError ? <Notice tone="error">Не удалось проверить открытые обращения. {errorText(active.error)} Создание заявки пока недоступно.</Notice> : hasActive ? <section className="duplicate-warning" aria-label="Открытые обращения абонента"><h3>У абонента уже есть открытые обращения</h3><p>Проверьте их перед созданием новой заявки.</p><ul>{active.data.items.map((ticket) => <li key={ticket.id}><div><strong>#{ticket.number} · {ticketTitle(ticket.subject)}</strong><span>{statusLabel[ticket.status]} · {formatDate(ticket.created_at)} · {ticket.assigned_engineer_name || 'Не назначен'}</span><p>{ticket.description}</p></div><Link to={`/tickets/${ticket.id}`}>Открыть существующую заявку <ArrowRight size={14} /></Link></li>)}</ul>{active.data.total_visible > active.data.page * active.data.size && <Button type="button" variant="quiet" onClick={() => setActivePage(activePage + 1)}>Следующие обращения</Button>}{activePage > 1 && <Button type="button" variant="quiet" onClick={() => setActivePage(activePage - 1)}>Предыдущие</Button>}{active.data.has_hidden_active && <p>Есть обращение, содержание которого вам недоступно. Передайте повторный звонок инженеру OSS для проверки; доступ к заявке определяется вашими правами.</p>}<label className="duplicate-warning__choice"><input type="checkbox" checked={otherProblem} onChange={(event) => { setOtherProblem(event.target.checked); edited() }} /> Другая проблема — создать отдельную заявку</label>{otherProblem && <label className="field"><span>Почему требуется новая заявка <em>*</em></span><textarea value={duplicateReason} onChange={(event) => { setDuplicateReason(event.target.value); edited() }} minLength={1} maxLength={500} rows={2} required placeholder="Кратко укажите отличие от открытого обращения" /></label>}</section> : null)}
          </div>}
          <div className="form-section-heading form-section-heading--later"><span className="step-number">{linked ? '03' : '02'}</span><div><h2>Содержание</h2></div></div>
          <div className="form-fields">
            {linked ? <>
              <label className="field"><span>Тема обращения <em>*</em></span><select aria-label="Тема обращения" value={categoryId} onChange={(event) => { setCategoryId(event.target.value); edited() }} required><option value="">Выберите тему</option>{categories.data?.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
              {categories.isError && <Notice tone="error">Не удалось загрузить темы обращений. {errorText(categories.error)}</Notice>}
              {selectedCategory?.is_other && <label className="field"><span>Уточнение темы <em>*</em></span><input id="ticket-subject" name="subject" value={subject} maxLength={160} placeholder="Кратко уточните тему" onChange={(event) => { setSubject(event.target.value); edited() }} required /></label>}
              <label className="field"><span>Комментарий <small>необязательно</small></span><textarea id="ticket-description" name="description" value={description} maxLength={10000} rows={3} placeholder="Дополнительные сведения со слов абонента" onChange={(event) => { setDescription(event.target.value); edited() }} /></label>
            </> : <>
              <label className="field"><span>Тема <small>необязательно</small></span><input id="ticket-subject" name="subject" value={subject} maxLength={160} placeholder="Краткая тема" onChange={(event) => { setSubject(event.target.value); edited() }} /></label>
              <label className="field"><span>Описание <em>*</em></span><textarea id="ticket-description" name="description" value={description} maxLength={10000} rows={7} required placeholder="Что произошло и какая работа требуется?" onChange={(event) => { setDescription(event.target.value); edited() }} /></label>
              <label className="field form-fields__priority"><span>Приоритет</span><select id="ticket-priority" name="priority" value={priority} onChange={(event) => { setPriority(event.target.value as TicketPriority); edited() }}>{priorities.map((item) => <option key={item} value={item}>{priorityLabel[item]}</option>)}</select></label>
            </>}
          </div>
          {mutation.isError && <Notice tone="error">{mutation.error instanceof ApiError && mutation.error.code === 'active_ticket_exists' ? 'Появилось открытое обращение этого абонента. Проверьте его и при другой проблеме укажите причину новой заявки.' : errorText(mutation.error)}</Notice>}
          <div className="create-actions"><span>Сохранение и история выполняются сервером в одной транзакции.</span><Button type="submit" variant="primary" disabled={mutation.isPending || !canSubmit}>{mutation.isPending ? 'Создаём…' : 'Создать заявку'} <ArrowRight size={16} /></Button></div>
        </form>
      </div>
    </div>
  )
}
