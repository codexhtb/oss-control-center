import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { ticketApi } from './ticket-api'
import type { ConfiguredService } from './types'
import { errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'

const statusLabels: Record<ConfiguredService['status'], string> = {
  active: 'Действует', blocked: 'Приостановлена в биллинге', planned: 'Запланирована',
  ended: 'Не действует (срок/удаление)', unknown: 'Состояние не определено',
}
const typeLabels: Record<ConfiguredService['type'], string> = {
  periodic: 'Периодическая услуга', ip_traffic: 'Услуга IP-трафика', unknown: 'Тип не определён',
}

function date(value: string | null) {
  return value ? new Date(value).toLocaleString('ru-RU') : null
}

function cost(value: string | null, currency: string | null) {
  if (value === null || !Number.isFinite(Number(value))) return 'Стоимость не определена'
  const formatted = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value))
  return `${formatted}${currency ? ` ${currency}` : ''}`
}

function ServiceItem({ item, onCharges }: { item: ConfiguredService; onCharges: (linkId: number, name: string) => void }) {
  return <article className="configured-service">
    <div className="configured-service__main"><div><h4>{item.name || 'Услуга без названия'}</h4><span>{typeLabels[item.type]} · {statusLabels[item.status]}</span></div>
      <div className="configured-service__price"><strong>{cost(item.configured_cost, item.currency)}</strong><span>{item.period || 'Расчётный период не подтверждён'}</span></div></div>
    {(item.starts_at || item.ends_at) && <p className="configured-service__dates">{item.starts_at && <>Начало действия связи: {date(item.starts_at)}</>}{item.ends_at && <> · Окончание: {date(item.ends_at)}</>}</p>}
    {item.can_filter_charges && <Button variant="quiet" onClick={() => onCharges(item.link_id, item.name || 'Услуга без названия')}>Показать фактические начисления</Button>}
    <details className="subscriber-technical"><summary>Технические данные</summary><p>Источник: NetUP · ID связи {item.link_id} · ID услуги {item.service_id}. Настроенная стоимость не равна фактическому списанию.</p></details>
  </article>
}

export function SubscriberServices({ lookupId, onCharges }: { lookupId: string; onCharges: (linkId: number, name: string) => void }) {
  const [historyOpen, setHistoryOpen] = useState(false)
  const services = useQuery({ queryKey: ['subscriber-services', lookupId], queryFn: ({ signal }) => ticketApi.subscriberServices(lookupId, signal) })
  const history = useQuery({ queryKey: ['subscriber-tariff-history', lookupId], queryFn: ({ signal }) => ticketApi.subscriberTariffHistory(lookupId, signal), enabled: historyOpen })
  const main = services.data?.items.filter((item) => item.role === 'main_tariff') || []
  const other = services.data?.items.filter((item) => item.role !== 'main_tariff') || []
  return <section className="subscriber-panel subscriber-panel--wide"><div className="subscriber-section-title"><h2>Услуги и тариф</h2>{services.data?.as_of && <span className="subscriber-section-note">NetUP · {date(services.data.as_of)}</span>}</div>
    {services.isPending && <p role="status" className="muted">Загружаем услуги…</p>}
    {services.isError && <Notice tone="error">Не удалось получить услуги NetUP: {errorText(services.error)}</Notice>}
    {services.data && <>
      {main.length > 0 && <div className="configured-service-group"><h3>Основной тариф</h3>{main.map((item) => <ServiceItem key={item.link_id} item={item} onCharges={onCharges} />)}</div>}
      {other.length > 0 && <div className="configured-service-group"><h3>{main.length ? 'Дополнительные услуги' : 'Услуги'}</h3>{other.map((item) => <ServiceItem key={item.link_id} item={item} onCharges={onCharges} />)}</div>}
      {!services.data.items.length && <p className="muted">Действующих связей услуг не найдено.</p>}
      {!main.length && !!other.length && <p className="subscriber-limitation">Основной тариф не удалось однозначно определить по связям NetUP.</p>}
      {services.data.items.some((item) => item.configured_cost !== null) && <p className="subscriber-limitation">Стоимость взята из текущей конфигурации NetUP. Длительность расчётного периода не подтверждена; фактические списания смотрите во вкладке «Платежи».</p>}
      {!services.data.items.every((item) => item.currency) && <p className="subscriber-limitation">Для услуг без подтверждённой валюты обозначение валюты не показано.</p>}
    </>}
    <details className="subscriber-technical" onToggle={(event) => setHistoryOpen(event.currentTarget.open)}><summary>История тарифов</summary>
      {historyOpen && history.isPending && <p role="status">Загружаем историю…</p>}
      {historyOpen && history.isError && <Notice tone="error">Не удалось получить историю тарифов: {errorText(history.error)}</Notice>}
      {history.data && (!history.data.available ? <p>Историю нельзя безопасно прочитать: запрос требует большого сканирования NetUP.</p> : <>
        {history.data.items.length ? <div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Тариф в записи NetUP</th><th>Начало</th><th>Окончание</th><th>Стоимость на тот момент</th></tr></thead><tbody>{history.data.items.map((item, index) => <tr key={`${item.starts_at}-${index}`}><td>{item.name || 'Название не сохранено'}</td><td>{date(item.starts_at) || '—'}</td><td>{date(item.ends_at) || '—'}</td><td>Не подтверждена</td></tr>)}</tbody></table></div> : <p>Записей смены тарифа нет.</p>}
        {history.data.truncated && <p className="subscriber-limitation">Показаны последние 50 записей.</p>}
        <p className="subscriber-limitation">Источник: NetUP.tariffs_history. Исторические цены здесь не сохранены; названия и даты не заменяют фактические начисления.</p>
      </>)}
    </details>
  </section>
}
