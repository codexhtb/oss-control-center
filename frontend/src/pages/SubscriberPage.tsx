import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowRight, Clock3, Search } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { formatDate, statusLabel, ticketTitle } from '../features/tickets/presentation'
import { ticketApi } from '../features/tickets/ticket-api'
import { MonitoringReport } from '../features/tickets/MonitoringReport'
import { SubscriberOnuCard } from '../features/tickets/OnuCard'
import type { DataQuality, FieldOrigin, SubscriberCard, SubscriberConnectionCard, SubscriberIpRow } from '../features/tickets/types'
import { configuredIpv4, formatSubscriberAddress } from '../features/tickets/subscriber-presentation'
import type { MeResponse } from '../shared/api/types'
import { errorText } from '../shared/api/client'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'

const qualityLabel: Record<DataQuality, string> = {
  KNOWN: 'Подтверждено источником', UNKNOWN: 'Неизвестно', STALE: 'Устарело',
  CONFLICT: 'Противоречие', UNAVAILABLE: 'Недоступно',
}

function Origin({ meta }: { meta?: FieldOrigin }) {
  if (!meta) return <span className="origin-chip origin-chip--unknown">Неизвестно</span>
  if (meta.quality === 'STALE') return <span className="origin-dot origin-dot--stale" role="img" aria-label={`Устарело · ${meta.source} · ${formatDate(meta.observed_at)}`} title={`Устарело · ${meta.source} · ${formatDate(meta.observed_at)}`} />
  return <span className={`origin-chip origin-chip--${meta.quality.toLowerCase()}`} title={`${meta.source} · ${formatDate(meta.observed_at)}`}>{qualityLabel[meta.quality]}</span>
}

function Fact({ label, value, meta }: { label: string; value: string | number | null | undefined; meta?: FieldOrigin }) {
  return <div className="subscriber-fact"><span>{label}</span><strong>{value === null || value === undefined || value === '' ? 'UNKNOWN' : value}</strong><Origin meta={meta} /></div>
}

function ConnectionBlock({ connection, service, lookupId, canCheck }: { connection: SubscriberConnectionCard; service: string; lookupId: string; canCheck: boolean }) {
  const check = useMutation({ mutationFn: () => ticketApi.checkSubscriber(lookupId, connection.connection_snapshot_id) })
  const addresses = configuredIpv4(connection.ip_groups.flatMap((group) => group.rows))
  const hasAutoPool = connection.ip_groups.some((group) => group.rows.some((row) => row.address_assignment === 'AUTO_POOL'))
  return <article className="subscriber-service">
    <div className="subscriber-service__head"><div><span className="eyebrow">ПОДКЛЮЧЕНИЕ · {service}</span><h3>{connection.device || 'Устройство неизвестно'}</h3></div><Origin meta={connection.field_meta.device} /></div>
    <div className="subscriber-facts subscriber-facts--three">
      <Fact label="Тип доступа" value={connection.access_type} meta={connection.field_meta.access_type} />
      <Fact label="Порт" value={connection.port} meta={connection.field_meta.port} />
      <Fact label="VLAN" value={connection.vlan} meta={connection.field_meta.vlan} />
      <Fact label="ONU" value={connection.onu_id} meta={connection.field_meta.onu_id} />
      <Fact label="IP-группа" value={connection.ip_group} meta={connection.field_meta.ip_group} />
      <Fact label="IPv4 в конфигурации NetUP" value={addresses.length ? addresses.join(', ') : 'IP не определён'} meta={connection.ip_groups.flatMap((group) => group.rows).find((row) => row.configured_ipv4)?.field_meta.configured_ipv4} />
      {hasAutoPool && <Fact label="Текущий динамический IP" value="IP не определён · нужна активная DHCP/RADIUS-сессия" />}
    </div>
    {connection.ip_groups.map((group) => <div key={group.group_id} className="ip-group"><h4>IP-группа {group.group_id} <span>{group.rows.length} записей</span></h4>
      <div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Запись</th><th>Адресация</th><th>DHCP</th><th>Коммутатор</th><th>Порт</th><th>VLAN</th></tr></thead><tbody>
        {group.rows.map((row: SubscriberIpRow) => <tr key={row.row_id}>
          <td>{row.row_id}</td>
          <td>{row.configured_ipv4 && row.configured_prefix_length !== null && row.configured_prefix_length !== undefined ? <>{row.configured_ipv4}/{row.configured_prefix_length} <Origin meta={row.field_meta.configured_ipv4} /></> : row.address_assignment === 'AUTO_POOL' ? '[Авто]' : 'IP не определён'}</td>
          <td>{row.dhcp_pool_id ? <>Пул {row.dhcp_pool_id} <Origin meta={row.field_meta.dhcp_pool_id} /></> : 'Пул не задан'}</td>
          <td>{row.device || 'UNKNOWN'} <Origin meta={row.field_meta.device} /></td>
          <td>{row.port_id || 'UNKNOWN'} <Origin meta={row.field_meta.port_id} /></td>
          <td>{row.vlan_id || 'UNKNOWN'} <Origin meta={row.field_meta.vlan_id} /></td>
        </tr>)}
      </tbody></table></div>
    </div>)}
    {canCheck && <div className="monitoring-action"><Button variant="secondary" disabled={check.isPending} onClick={() => check.mutate()}>{check.isPending ? 'Проверяем…' : 'Проверить подключение'}</Button></div>}
    {check.isError && <Notice tone="error">{errorText(check.error)}</Notice>}
    {check.data && <MonitoringReport report={check.data} />}
    {canCheck && connection.access_type !== 'ETHERNET' && <SubscriberOnuCard lookupId={lookupId} connectionId={connection.connection_snapshot_id} />}
  </article>
}

function SubscriberDetails({ card, session }: { card: SubscriberCard; session: MeResponse }) {
  const [historyPage, setHistoryPage] = useState(1)
  const history = useQuery({ queryKey: ['subscriber-tickets', card.lookup_id, historyPage], queryFn: ({ signal }) => ticketApi.subscriberTickets(card.lookup_id, historyPage, signal) })
  const subscriber = card.subscriber
  const serviceNames = new Map(card.services.map((service) => [service.service_id, service.name || `Услуга ${service.service_id}`]))
  const sourceDate = subscriber.field_meta.billing_account?.observed_at || card.fetched_at
  return <>
    <div className="subscriber-heading"><div><p className="eyebrow">КАРТОЧКА АБОНЕНТА</p><h1>{subscriber.full_name || subscriber.organization_name || 'Имя неизвестно'}</h1><div className="subscriber-heading__meta"><span>ЛС {subscriber.billing_account}</span><span>Источник {card.source}</span><span><Clock3 size={14} /> {formatDate(sourceDate)}</span></div></div>
      {session.permissions.includes('tickets.create') && <Link className="button button--primary" to={`/tickets/new?lookup=${encodeURIComponent(card.lookup_id)}`}>Создать заявку <ArrowRight size={16} /></Link>}
    </div>
    {subscriber.field_meta.billing_account?.quality === 'STALE' && <Notice tone="error">Данные лабораторной копии устарели. Сведения о состоянии услуг и балансе относятся к дате источника.</Notice>}
    {card.is_expired && <Notice tone="error">Для создания заявки карточку нужно обновить повторным поиском.</Notice>}
    <div className="subscriber-columns"><section className="subscriber-panel"><div className="subscriber-section-title"><h2>Абонент и контакты</h2><Origin meta={subscriber.field_meta.full_name} /></div>
      <div className="subscriber-facts"><Fact label="ID абонента" value={subscriber.subscriber_id} meta={subscriber.field_meta.subscriber_id} /><Fact label="Адрес в биллинге" value={formatSubscriberAddress(subscriber.actual_address, subscriber.house_number)} meta={subscriber.field_meta.actual_address} /><Fact label="E-mail" value={subscriber.email} meta={subscriber.field_meta.email} /></div>
      <h3>Телефоны</h3>{subscriber.contacts.length ? <div className="contact-list">{subscriber.contacts.map((contact, index) => <div key={`${contact.type}-${index}`}><span>{({ mobile: 'Мобильный', work: 'Рабочий', home: 'Домашний' })[contact.type]}</span><strong>{contact.value}</strong><Origin meta={{ source: card.source as 'MOCK' | 'NETUP', observed_at: contact.observed_at, quality: contact.quality }} /></div>)}</div> : <p className="muted">Контакты UNKNOWN</p>}
    </section><section className="subscriber-panel"><div className="subscriber-section-title"><h2>Счета и договоры</h2><Origin meta={subscriber.field_meta.contracts} /></div>
      <div className="account-list">{card.accounts.length ? card.accounts.map((account) => <div key={account.account_id}><strong>ЛС {account.account_id}</strong><span>{account.is_primary ? 'Основной' : 'Дополнительный'}</span><Origin meta={account.field_meta.account_id} /></div>) : <p className="muted">Счета не представлены источником</p>}</div>
      <h3>Договоры</h3>{card.contracts.length ? card.contracts.map((contract) => <p key={contract.contract_id}>{contract.label || contract.contract_id}</p>) : <p className="muted">Номер договора UNKNOWN</p>}
    </section></div>
    {session.permissions.includes('subscribers.finance.read') && <section className="subscriber-panel subscriber-panel--wide"><div className="subscriber-section-title"><h2>Финансовые сведения</h2><span className="subscriber-section-note">Только данные биллинга · не состояние подключения</span></div>
      {card.financial_accounts.length ? <div className="subscriber-facts subscriber-facts--three">{card.financial_accounts.map((account) => <div key={account.account_id} className="finance-account"><h3>ЛС {account.account_id}</h3><Fact label="Баланс · единица UTM5" value={account.balance} meta={account.field_meta.balance} /><Fact label="Кредит · единица UTM5" value={account.credit} meta={account.field_meta.credit} /><Fact label="Блокировка счёта" value={account.account_blocked === null ? null : account.account_blocked ? 'Да' : 'Нет'} meta={account.field_meta.account_blocked} /></div>)}</div> : <p className="muted">Финансовые данные UNKNOWN</p>}
    </section>}
    <section className="subscriber-panel subscriber-panel--wide"><div className="subscriber-section-title"><h2>Услуги и тарифы</h2><span className="subscriber-section-note">Состояние на дату источника</span></div>
      {card.services.length ? <div className="service-list">{card.services.map((service) => <article key={service.service_id} className="service-row"><div><span className="eyebrow">СВЯЗКА {service.service_id} · ЛС {service.account_id || 'UNKNOWN'}</span><h3>{service.name || 'Услуга без названия'}</h3><span>Тариф: {service.tariff_name || 'UNKNOWN'} <Origin meta={service.field_meta.tariff_name} /></span></div><div className="service-row__state"><span>{service.billing_active ? 'Действует в биллинге' : 'Статус UNKNOWN'}</span><Origin meta={service.field_meta.billing_active} />{service.billing_blocked && <span>Блокировка услуги в биллинге</span>}</div></article>)}</div> : <p className="muted">Действующие услуги в источнике не найдены</p>}
    </section>
    <section className="subscriber-panel subscriber-panel--wide"><div className="subscriber-section-title"><h2>Подключения и IP-группы</h2></div>
      {card.connections.length ? <div className="connection-list">{card.connections.map((connection) => <ConnectionBlock key={connection.connection_snapshot_id} connection={connection} service={serviceNames.get(connection.service_id || '') || 'UNKNOWN'} lookupId={card.lookup_id} canCheck={session.permissions.includes('diagnostics.run')} />)}</div> : <p className="muted">Данные о подключении UNKNOWN</p>}
    </section>
    <section className="subscriber-panel subscriber-panel--wide"><div className="subscriber-section-title"><h2>История обращений</h2><span className="subscriber-section-note">Связанные заявки по этому абоненту</span></div>
      {history.isPending ? <p role="status" className="muted">Загружаем обращения…</p> : history.isError ? <Notice tone="error">{errorText(history.error)}</Notice> : history.data?.items.length ? <><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Номер</th><th>Тема</th><th>Статус</th><th>Создана</th></tr></thead><tbody>{history.data.items.map((ticket) => <tr key={ticket.id}><td><Link to={`/tickets/${ticket.id}`}>#{ticket.number}</Link></td><td>{ticketTitle(ticket.subject)}</td><td>{statusLabel[ticket.status]}</td><td>{formatDate(ticket.created_at)}</td></tr>)}</tbody></table></div><div className="subscriber-pagination"><Button variant="quiet" disabled={historyPage <= 1} onClick={() => setHistoryPage(historyPage - 1)}>Назад</Button><span>{historyPage} / {Math.ceil(history.data.total / history.data.size)}</span><Button variant="quiet" disabled={historyPage * history.data.size >= history.data.total} onClick={() => setHistoryPage(historyPage + 1)}>Далее</Button></div></> : <p className="muted">Связанных обращений пока нет</p>}
    </section>
  </>
}

export function SubscriberPage({ session }: { session: MeResponse }) {
  const { lookupId } = useParams()
  const navigate = useNavigate()
  const [accountNo, setAccountNo] = useState('')
  const lookup = useMutation({ mutationFn: ticketApi.lookup, onSuccess: (card) => navigate(`/subscribers/${card.lookup_id}`) })
  const card = useQuery({ queryKey: ['subscriber', lookupId], queryFn: ({ signal }) => ticketApi.getLookup(lookupId!, signal), enabled: !!lookupId })
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (accountNo.trim() && !lookup.isPending) lookup.mutate(accountNo.trim()) }
  return <div className="page desk-page subscriber-page">
    <form className="subscriber-search" onSubmit={submit}><label className="field" htmlFor="subscriber-account"><span>Поиск по лицевому счёту</span><input id="subscriber-account" name="account_no" value={accountNo} onChange={(event) => setAccountNo(event.target.value)} maxLength={100} autoComplete="off" placeholder="Введите номер ЛС" /></label><Button type="submit" variant="primary" icon={<Search size={16} />} disabled={!accountNo.trim() || lookup.isPending}>{lookup.isPending ? 'Поиск…' : 'Найти'}</Button></form>
    {lookup.isError && <Notice tone="error">{errorText(lookup.error)}</Notice>}
    {!lookupId && <div className="subscriber-empty"><Search size={28} /><h1>Subscriber 360</h1><p>Введите лицевой счёт, чтобы открыть карточку абонента.</p></div>}
    {lookupId && (card.isPending ? <p role="status" className="muted">Загружаем карточку…</p> : card.isError ? <Notice tone="error">{errorText(card.error)}</Notice> : card.data ? <SubscriberDetails key={lookupId} card={card.data} session={session} /> : null)}
  </div>
}
