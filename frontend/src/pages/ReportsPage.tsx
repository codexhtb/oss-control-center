import { useQuery } from '@tanstack/react-query'
import { BarChart3 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { getGeneralReport, type GeneralReport } from '../features/reports/report-api'
import { errorText } from '../shared/api/client'
import { Notice } from '../shared/ui/Notice'
import { Button } from '../shared/ui/Button'
import { formatDate, priorityLabel, statusLabel, ticketTitle, typeLabel } from '../features/tickets/presentation'

type Preset = 'today' | 'yesterday' | 'week' | 'month' | 'previous' | 'custom'
const labels: Record<Preset, string> = { today: 'Сегодня', yesterday: 'Вчера', week: '7 дней', month: 'Текущий месяц', previous: 'Предыдущий месяц', custom: 'Период' }

function localDate(value: Date) {
  return `${value.getUTCFullYear()}-${String(value.getUTCMonth() + 1).padStart(2, '0')}-${String(value.getUTCDate()).padStart(2, '0')}`
}

export function periodFor(preset: Exclude<Preset, 'custom'>, now = new Date()) {
  const local = new Date(now.getTime() + 3 * 60 * 60 * 1000)
  const day = new Date(Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate()))
  const shift = (days: number) => localDate(new Date(day.getTime() + days * 86_400_000))
  if (preset === 'today') return { start: shift(0), end: shift(0) }
  if (preset === 'yesterday') return { start: shift(-1), end: shift(-1) }
  if (preset === 'week') return { start: shift(-6), end: shift(0) }
  if (preset === 'month') return { start: localDate(new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), 1))), end: shift(0) }
  return { start: localDate(new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth() - 1, 1))), end: localDate(new Date(Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), 0))) }
}

function duration(seconds: number | null) {
  if (seconds === null) return 'Нет событий'
  const hours = seconds / 3600
  return hours < 1 ? `${Math.round(seconds / 60)} мин` : `${hours.toFixed(1)} ч`
}

function Trend({ data }: { data: GeneralReport['daily'] }) {
  if (!data.length) return null
  const width = 680, height = 180, pad = 18
  const max = Math.max(1, ...data.flatMap((point) => [point.created, point.closed]))
  const line = (kind: 'created' | 'closed') => data.map((point, index) => {
    const x = pad + index * (width - 2 * pad) / Math.max(1, data.length - 1)
    const y = height - pad - point[kind] * (height - 2 * pad) / max
    return `${x},${y}`
  }).join(' ')
  return <div className="reports-trend"><div className="reports-legend"><span>● Поступили</span><span>● Закрыты</span></div><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Динамика поступивших и закрытых заявок по дням" preserveAspectRatio="none"><polyline points={line('created')} className="reports-trend__created" /><polyline points={line('closed')} className="reports-trend__closed" />{data.length === 1 && <><circle cx={pad} cy={height - pad - (data[0]?.created ?? 0) * (height - 2 * pad) / max} r="4" className="reports-trend__created-dot" /><circle cx={pad} cy={height - pad - (data[0]?.closed ?? 0) * (height - 2 * pad) / max} r="4" className="reports-trend__closed-dot" /></>}</svg><div className="reports-trend__dates"><span>{data[0]?.date}</span><span>{data[data.length - 1]?.date}</span></div></div>
}

function Distribution({ title, data, names }: { title: string; data: Record<string, number>; names: Record<string, string> }) {
  const total = Object.values(data).reduce((sum, item) => sum + item, 0)
  return <section className="reports-panel"><h2>{title}</h2>{total === 0 ? <p className="muted">За период заявок нет</p> : Object.entries(data).sort((a, b) => b[1] - a[1]).map(([key, count]) => <div className="reports-distribution" key={key}><div><span>{names[key] || key}</span><strong>{count}</strong></div><div className="reports-distribution__track"><span style={{ width: `${count / total * 100}%` }} /></div></div>)}</section>
}

export function ReportsPage() {
  const [preset, setPreset] = useState<Preset>('week')
  const [range, setRange] = useState(() => periodFor('week'))
  const [page, setPage] = useState(1)
  const valid = !!range.start && !!range.end && range.start <= range.end && (Date.parse(range.end) - Date.parse(range.start)) / 86_400_000 <= 3660
  const report = useQuery({ queryKey: ['reports', 'general', range, page], queryFn: ({ signal }) => getGeneralReport(range.start, range.end, page, signal), enabled: valid })
  function choose(value: Preset) { setPreset(value); setPage(1); if (value !== 'custom') setRange(periodFor(value)) }
  return <div className="reports-page">
    <div className="page-heading"><div><p className="eyebrow">SERVICE DESK</p><h1>Отчёты</h1></div><BarChart3 size={25} aria-hidden="true" /></div>
    <section className="reports-filters" aria-label="Период отчёта"><div className="reports-presets">{(Object.keys(labels) as Preset[]).map((value) => <button type="button" key={value} className={preset === value ? 'reports-preset reports-preset--active' : 'reports-preset'} onClick={() => choose(value)}>{labels[value]}</button>)}</div>{preset === 'custom' && <div className="reports-range"><label>С <input type="date" value={range.start} onChange={(event) => { setRange({ ...range, start: event.target.value }); setPage(1) }} /></label><label>По <input type="date" value={range.end} onChange={(event) => { setRange({ ...range, end: event.target.value }); setPage(1) }} /></label></div>}<small>Даты и графики: UTC+3. Поступления и детализация — по дате создания.</small></section>
    {!valid && <Notice tone="error">Выберите корректный период не длиннее 10 лет.</Notice>}
    {report.isPending && valid && <p role="status">Загружаем отчёт…</p>}
    {report.isError && <Notice tone="error">Не удалось получить отчёт. {errorText(report.error)}</Notice>}
    {report.data && <>
      <div className="reports-metrics">
        <article><span>Поступили за период</span><strong>{report.data.metrics.created}</strong></article>
        <article><span>Решения за период</span><strong>{report.data.metrics.resolved_events}</strong></article>
        <article><span>Закрытия за период</span><strong>{report.data.metrics.closed_events}</strong></article>
        <article><span>Вернули инженерам</span><strong>{report.data.metrics.returned_events}</strong></article>
        <article><span>Активны сейчас</span><strong>{report.data.metrics.active_now}</strong><small>Независимо от периода</small></article>
        <article><span>Среднее до решения</span><strong>{duration(report.data.metrics.average_resolution_seconds)}</strong></article>
        <article><span>Среднее до закрытия</span><strong>{duration(report.data.metrics.average_closure_seconds)}</strong></article>
      </div>
      <p className="reports-explanation">Решения, закрытия и возвраты считаются как события. Одна заявка может пройти несколько циклов. Среднее до решения — от создания или последнего возврата; до закрытия — от создания заявки.</p>
      <section className="reports-panel"><h2>Динамика по дням</h2><Trend data={report.data.daily} /></section>
      <div className="reports-distributions"><Distribution title="Типы поступивших" data={report.data.distribution.types} names={typeLabel} /><Distribution title="Текущие статусы поступивших" data={report.data.distribution.statuses} names={statusLabel} /><Distribution title="Приоритеты поступивших" data={report.data.distribution.priorities} names={priorityLabel} /></div>
      <section className="reports-panel"><div className="reports-panel__heading"><h2>Поступившие заявки</h2><span>{report.data.details.total}</span></div><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>№</th><th>Тема</th><th>Тип</th><th>Статус</th><th>Приоритет</th><th>Исполнитель</th><th>Создана</th></tr></thead><tbody>{report.data.details.items.map((item) => <tr key={item.id}><td><Link to={`/tickets/${item.id}`}>#{item.number}</Link></td><td>{ticketTitle(item.subject)}</td><td>{typeLabel[item.ticket_type as keyof typeof typeLabel] || item.ticket_type}</td><td>{statusLabel[item.status as keyof typeof statusLabel] || item.status}</td><td>{priorityLabel[item.priority as keyof typeof priorityLabel] || item.priority}</td><td>{item.assignee_name || 'Не назначен'}</td><td>{formatDate(item.created_at)}</td></tr>)}</tbody></table></div>{report.data.details.total === 0 && <p className="muted">За выбранный период заявок нет.</p>}<div className="reports-pagination"><Button variant="quiet" disabled={page <= 1} onClick={() => setPage(page - 1)}>Назад</Button><span>{page} / {Math.max(1, Math.ceil(report.data.details.total / 20))}</span><Button variant="quiet" disabled={page * 20 >= report.data.details.total} onClick={() => setPage(page + 1)}>Далее</Button></div></section>
    </>}
  </div>
}
