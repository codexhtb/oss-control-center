import { useQuery } from '@tanstack/react-query'
import { ChevronRight } from 'lucide-react'
import { Fragment, useState } from 'react'
import { ticketApi } from './ticket-api'
import type { SubscriberFinanceKind } from './types'
import { errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'

const labels: Record<string, string> = {
  cash: 'Поступление', credit: 'Изменение кредитного лимита', canceled: 'Отмена / сторно',
  unclassified_negative: 'Отрицательная операция', unclassified_zero: 'Операция без суммы',
  one_off: 'Разовая услуга', periodic: 'Периодическая услуга',
  ip_traffic: 'IP-трафик', charge_refund: 'Уменьшение начисления',
  adjustment: 'Корректировка', other: 'Другая операция',
}

function dateInput(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function range(preset: string, from: string, to: string) {
  const now = new Date()
  let start: Date
  let end: Date
  if (preset === 'custom') {
    start = new Date(`${from}T00:00:00`)
    end = new Date(`${to}T00:00:00`)
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return { start: '', end: '', valid: false }
    end.setDate(end.getDate() + 1)
  } else {
    end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1)
    start = preset === 'month' ? new Date(now.getFullYear(), now.getMonth(), 1)
      : preset === 'three' ? new Date(now.getFullYear(), now.getMonth() - 3, now.getDate())
        : new Date(end.getTime() - 365 * 86400000)
  }
  return { start: start.toISOString(), end: end.toISOString(), valid: start < end && end.getTime() - start.getTime() <= 366 * 86400000 }
}

function cash(value: string, currency: string | null) {
  const result = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value))
  return `${result}${currency ? ` ${currency}` : ''}`
}

export function SubscriberFinance({ lookupId, showTechnical }: { lookupId: string; showTechnical: boolean }) {
  const [preset, setPreset] = useState('month')
  const [from, setFrom] = useState(dateInput(new Date(new Date().getFullYear(), new Date().getMonth(), 1)))
  const [to, setTo] = useState(dateInput(new Date()))
  const [kind, setKind] = useState<SubscriberFinanceKind>('payments')
  const [page, setPage] = useState(1)
  const [expanded, setExpanded] = useState<string | null>(null)
  const period = range(preset, from, to)
  const query = useQuery({
    queryKey: ['subscriber-finance', lookupId, period.start, period.end, kind, page],
    queryFn: ({ signal }) => ticketApi.subscriberFinance(lookupId, period.start, period.end, kind, page, signal),
    enabled: period.valid,
  })
  const data = query.data
  function changePreset(next: string) { setPreset(next); setPage(1); setExpanded(null) }
  function changeKind(next: SubscriberFinanceKind) { setKind(next); setPage(1); setExpanded(null) }
  return <section className="subscriber-panel subscriber-panel--wide subscriber-finance-history">
    <div className="subscriber-section-title"><h2>Платежи и начисления</h2></div>
    <div className="subscriber-finance-filters">
      <label>Период <select value={preset} onChange={(event) => changePreset(event.target.value)}>
        <option value="month">Текущий месяц</option><option value="three">Последние 3 месяца</option>
        <option value="year">Последний год</option><option value="custom">Выбрать даты</option>
      </select></label>
      {preset === 'custom' && <><label>С <input aria-label="С даты" type="date" value={from} onChange={(event) => { setFrom(event.target.value); setPage(1) }} /></label>
        <label>По <input aria-label="По дату" type="date" value={to} onChange={(event) => { setTo(event.target.value); setPage(1) }} /></label></>}
      <label>Операции <select value={kind} onChange={(event) => changeKind(event.target.value as SubscriberFinanceKind)}>
        <option value="payments">Платежи</option><option value="charges">Начисления</option><option value="credit">Кредит</option>
      </select></label>
    </div>
    {!period.valid && <Notice tone="error">Выберите период не длиннее одного года.</Notice>}
    {query.isPending && period.valid && <p role="status" className="muted">Загружаем операции…</p>}
    {query.isError && <Notice tone="error">{errorText(query.error)}</Notice>}
    {data && <>
      <div className="subscriber-finance-summary-title"><strong>Итоги за выбранный период</strong><span>Фильтр операций влияет только на список</span></div>
      <div className="subscriber-finance-summary">
        <div><span>Денежные поступления</span><strong>{cash(data.summary.cash, data.currency)}</strong></div>
        <div><span>Начисления</span><strong>{cash(data.summary.charges, data.currency)}</strong></div>
        <div><span>Операции кредита</span><strong>{cash(data.summary.credit, data.currency)}</strong></div>
      </div>
      {(Number(data.summary.adjustments) !== 0 || Number(data.summary.unclassified_negative) !== 0) &&
        <p className="subscriber-limitation">Корректировки начислений: {cash(data.summary.adjustments, data.currency)}. Неклассифицированные отрицательные операции: {cash(data.summary.unclassified_negative, data.currency)}. Эти суммы не входят в поступления и начисления.</p>}
      {!data.currency && <p className="subscriber-limitation">Валюта счёта не подтверждена источником. Суммы показаны без обозначения валюты.</p>}
      {data.items.length ? <div className="table-scroll"><table className="subscriber-table subscriber-finance-table"><thead><tr><th>Дата</th><th>Операция</th><th>Сумма</th><th>Услуга / способ</th></tr></thead><tbody>
        {data.items.map((item) => {
          const rowKey = `${kind}-${item.id}`
          const detailId = `finance-detail-${rowKey}`
          const isExpanded = expanded === rowKey
          const date = new Date(item.at).toLocaleString('ru-RU')
          return <Fragment key={rowKey}>
            <tr className={`subscriber-finance-row${isExpanded ? ' subscriber-finance-row--expanded' : ''}`} onClick={() => setExpanded(isExpanded ? null : rowKey)}>
              <td><button type="button" className="subscriber-finance-row__toggle" aria-label={`Подробности операции ${date}, ${cash(item.amount, item.currency)}`} aria-expanded={isExpanded} aria-controls={detailId} onClick={(event) => { event.stopPropagation(); setExpanded(isExpanded ? null : rowKey) }}><ChevronRight size={14} aria-hidden="true" /><span>{date}</span></button></td>
              <td>{labels[item.type] || 'Другая операция'}{item.canceled && item.type !== 'canceled' && <span className="subscriber-finance-canceled"> · отменена</span>}</td>
              <td className={item.canceled ? 'subscriber-finance-canceled subscriber-finance-row__amount' : 'subscriber-finance-row__amount'}>{cash(item.amount, item.currency)}</td>
              <td>{kind === 'credit' ? '—' : item.service || item.method || 'Не установлено'}</td>
            </tr>
            <tr className="subscriber-finance-row__details" hidden={!isExpanded}><td colSpan={4}><div id={detailId}>
              <span>{item.canceled ? 'Отменена или сторнирована' : 'Действующая операция'}</span>
              {item.method && <span>Метод NetUP: {item.method}</span>}
              {item.transaction_currency && item.transaction_amount && <span>Сумма операции: {cash(item.transaction_amount, item.transaction_currency)}</span>}
              {item.expires_at && <span>Срок действия: {new Date(item.expires_at).toLocaleString('ru-RU')}</span>}
              {showTechnical && <><span>Номер операции: {item.id}</span>{Object.entries(item.details).map(([key, value]) => <span key={key}>{key}: {String(value ?? '—')}</span>)}</>}
            </div></td></tr>
          </Fragment>
        })}
      </tbody></table></div> : <p className="muted">За выбранный период операций этого типа нет.</p>}
      {data.total > data.size && <div className="subscriber-pagination"><Button variant="quiet" disabled={page <= 1 || query.isFetching} onClick={() => { setPage(page - 1); setExpanded(null) }}>Назад</Button><span>{page} / {Math.ceil(data.total / data.size)}</span><Button variant="quiet" disabled={page * data.size >= data.total || query.isFetching} onClick={() => { setPage(page + 1); setExpanded(null) }}>Далее</Button></div>}
      <details className="subscriber-technical subscriber-finance-time"><summary>О времени операций</summary><p>Даты показаны в часовом поясе вашего устройства. Время биллинга пока не подтверждено.</p></details>
    </>}
  </section>
}
