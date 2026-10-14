import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, FilePlus2, Layers3, UserRound } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import type { MeResponse } from '../shared/api/types'
import { errorText } from '../shared/api/client'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { ticketApi } from '../features/tickets/ticket-api'
import { priorityLabel } from '../features/tickets/presentation'
import type { CreateTicketInput, TicketPriority, TicketType } from '../features/tickets/types'

const priorities: TicketPriority[] = ['LOW', 'NORMAL', 'HIGH', 'URGENT']

export function TicketCreatePage({ session }: { session: MeResponse }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [type, setType] = useState<TicketType>('MANUAL')
  const [subject, setSubject] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<TicketPriority>('NORMAL')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const mutation = useMutation({
    mutationFn: (payload: CreateTicketInput) => ticketApi.create(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['tickets'] })
      void queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      navigate('/tickets')
    },
  })
  const canCreate = session.permissions.includes('ticket.create')

  function edited() {
    setRequestId(crypto.randomUUID())
    mutation.reset()
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canCreate || mutation.isPending || !description.trim() || type === 'SUBSCRIBER_LINKED') return
    const payload: CreateTicketInput = {
      ticket_type: type,
      subject: subject.trim() || undefined,
      description: description.trim(),
      priority,
      client_request_id: requestId,
    }
    mutation.mutate(payload)
  }

  if (!canCreate) return <Notice tone="error">У вашей роли нет права создавать заявки.</Notice>
  return (
    <div className="page desk-page">
      <Link to="/tickets" className="back-link"><ArrowLeft size={15} /> К реестру заявок</Link>
      <div className="page-heading desk-heading"><div><p className="eyebrow">SERVICEDESK / СОЗДАНИЕ</p><h1>Новая заявка</h1><p className="page-heading__description">Выберите тип и опишите задачу. Дополнительные поля не обязательны.</p></div></div>
      <div className="create-layout">
        <form className="create-card" onSubmit={submit}>
          <div className="form-section-heading"><span className="step-number">01</span><div><h2>Тип заявки</h2><p>Общий workflow для обращений и внутренних задач.</p></div></div>
          <div className="type-picker" role="group" aria-label="Тип заявки">
            <button type="button" className={`type-option ${type === 'MANUAL' ? 'type-option--selected' : ''}`} onClick={() => { setType('MANUAL'); edited() }}><FilePlus2 size={20} /><strong>Ручная</strong><span>Обращение без привязки к NetUP</span></button>
            <button type="button" className={`type-option ${type === 'INTERNAL' ? 'type-option--selected' : ''}`} onClick={() => { setType('INTERNAL'); edited() }}><Layers3 size={20} /><strong>Внутренняя</strong><span>Задача команды OSS</span></button>
            <button type="button" className="type-option" disabled title="Поиск абонента будет добавлен следующим шагом"><UserRound size={20} /><strong>По абоненту</strong><span>Из Subscriber 360</span></button>
          </div>
          <div className="form-section-heading form-section-heading--later"><span className="step-number">02</span><div><h2>Содержание</h2><p>Свободный текст, без обязательного шаблона.</p></div></div>
          <div className="form-fields">
            <label className="field"><span>Тема <small>необязательно</small></span><input id="ticket-subject" name="subject" value={subject} maxLength={160} placeholder="Например, проверить доступность узла" onChange={(event) => { setSubject(event.target.value); edited() }} /></label>
            <label className="field"><span>Описание <em>*</em></span><textarea id="ticket-description" name="description" value={description} maxLength={10000} rows={7} required placeholder="Что произошло и какая работа требуется?" onChange={(event) => { setDescription(event.target.value); edited() }} /></label>
            <label className="field form-fields__priority"><span>Приоритет</span><select id="ticket-priority" name="priority" value={priority} onChange={(event) => { setPriority(event.target.value as TicketPriority); edited() }}>{priorities.map((item) => <option key={item} value={item}>{priorityLabel[item]}</option>)}</select></label>
          </div>
          {mutation.isError && <Notice tone="error">{errorText(mutation.error)} При сомнении проверьте реестр перед повтором.</Notice>}
          <div className="create-actions"><span>Сохранение и история выполняются сервером в одной транзакции.</span><Button type="submit" variant="primary" disabled={mutation.isPending || !description.trim()}>{mutation.isPending ? 'Создаём…' : 'Создать заявку'} <ArrowRight size={16} /></Button></div>
        </form>
        <aside className="create-aside"><div className="aside-kicker">КАК ЭТО РАБОТАЕТ</div><h3>Один реестр для всех задач</h3><p>Заявка получает номер, историю и единый маршрут работы. Исполнителя и решение можно добавить после создания.</p><div className="aside-rule" /><p className="muted">Для ручной и внутренней заявки данные NetUP не подставляются. Неизвестное техническое состояние остаётся неизвестным.</p></aside>
      </div>
    </div>
  )
}
