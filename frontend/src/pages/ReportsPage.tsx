import { useQuery } from '@tanstack/react-query'
import { BarChart3 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { getCallbacksReport, getEmployeesReport, getFieldWorkReport, getGeneralReport, getReasonsReport, type CallbacksReport, type EmployeesReport, type FieldWorkReport, type GeneralReport, type ReasonsReport } from '../features/reports/report-api'
import { errorText } from '../shared/api/client'
import { Notice } from '../shared/ui/Notice'
import { Button } from '../shared/ui/Button'
import { formatDate, priorityLabel, statusLabel, ticketTitle, typeLabel } from '../features/tickets/presentation'

type Preset = 'today' | 'yesterday' | 'week' | 'month' | 'previous' | 'custom'
type ReportTab = 'general' | 'employees' | 'callbacks' | 'field-work' | 'reasons'
const outcomeNames: Record<string, string> = { CONFIRMED: 'Проблема устранена', STILL_BROKEN: 'Проблема осталась', NO_ANSWER: 'Не дозвонились' }
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

function Breakdown({ title, items, missing }: { title: string; items: { name: string | null; count: number }[]; missing: string }) {
  const total = items.reduce((sum, item) => sum + item.count, 0)
  return <section className="reports-panel"><h2>{title}</h2>{total === 0 ? <p className="muted">За период событий нет.</p> : items.map((item, index) => <div className="reports-distribution" key={`${item.name}-${index}`}><div><span>{item.name ?? missing}</span><strong>{item.count}</strong></div><div className="reports-distribution__track"><span style={{ width: `${item.count / total * 100}%` }} /></div></div>)}</section>
}

function EmployeesContent({ data }: { data: EmployeesReport }) {
  const [selectedId, setSelectedId] = useState('')
  const selected = data.items.find((item) => item.user_id === selectedId) ?? data.items[0]
  const daily = data.daily.filter((item) => item.user_id === selected?.user_id)
  return <>
    <p className="reports-explanation">Созданные заявки считаются по автору, решения и звонки — по фактическим исполнителям. Повторные решения и звонки учтены как отдельные события.</p>
    <section className="reports-panel"><h2>Действия сотрудников</h2><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Сотрудник</th><th>Созданы заявки</th><th>Решения</th><th>Звонки</th><th>Уникальные заявки со звонками</th><th>Устранена</th><th>Осталась</th><th>Нет ответа</th></tr></thead><tbody>{data.items.map((item) => <tr key={item.user_id}><td>{item.display_name}{!item.is_active && <small className="muted"> · отключён</small>}</td><td>{item.created_tickets}</td><td>{item.decision_events}</td><td>{item.callback_events}</td><td>{item.callback_tickets}</td><td>{item.outcomes.CONFIRMED ?? 0}</td><td>{item.outcomes.STILL_BROKEN ?? 0}</td><td>{item.outcomes.NO_ANSWER ?? 0}</td></tr>)}</tbody></table></div>{data.items.length === 0 && <p className="muted">Нет действий за выбранный период.</p>}</section>
    {selected && <section className="reports-panel"><div className="reports-panel__heading"><h2>Динамика действий</h2><label>Сотрудник <select value={selected.user_id} onChange={(event) => setSelectedId(event.target.value)}>{data.items.map((item) => <option key={item.user_id} value={item.user_id}>{item.display_name}</option>)}</select></label></div><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Дата (UTC+3)</th><th>Созданы</th><th>Решения</th><th>Звонки</th></tr></thead><tbody>{daily.map((item) => <tr key={item.date}><td>{item.date}</td><td>{item.created}</td><td>{item.decisions}</td><td>{item.callbacks}</td></tr>)}</tbody></table></div>{daily.length === 0 && <p className="muted">За период действий нет.</p>}</section>}
  </>
}

function CallbacksContent({ data, page, setPage }: { data: CallbacksReport; page: number; setPage: (page: number) => void }) {
  return <>
    <div className="reports-metrics"><article><span>Контрольные звонки</span><strong>{data.total_events}</strong></article><article><span>Уникальные заявки</span><strong>{data.unique_tickets}</strong></article></div>
    <p className="reports-explanation">Повторные контрольные звонки учитываются как отдельные события. Дата — фактическое время звонка в UTC+3.</p>
    <Distribution title="Результаты звонков" data={data.outcomes} names={outcomeNames} />
    <div className="reports-distributions reports-distributions--two"><section className="reports-panel"><h2>Динамика по датам</h2><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Дата</th><th>Результат</th><th>Звонки</th></tr></thead><tbody>{data.daily.map((item) => <tr key={`${item.date}-${item.outcome}`}><td>{item.date}</td><td>{outcomeNames[item.outcome] ?? item.outcome}</td><td>{item.count}</td></tr>)}</tbody></table></div>{data.daily.length === 0 && <p className="muted">За период звонков нет.</p>}</section>
    <section className="reports-panel"><h2>По сотрудникам</h2><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Сотрудник</th><th>Результат</th><th>Звонки</th></tr></thead><tbody>{data.employees.map((item) => <tr key={`${item.user_id}-${item.outcome}`}><td>{item.display_name}</td><td>{outcomeNames[item.outcome] ?? item.outcome}</td><td>{item.count}</td></tr>)}</tbody></table></div>{data.employees.length === 0 && <p className="muted">За период звонков нет.</p>}</section></div>
    <section className="reports-panel"><div className="reports-panel__heading"><h2>Журнал звонков</h2><span>{data.details.total}</span></div><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Заявка</th><th>Тема</th><th>Сотрудник</th><th>Результат</th><th>Когда</th></tr></thead><tbody>{data.details.items.map((item) => <tr key={item.id}><td><Link to={`/tickets/${item.ticket_id}`}>#{item.ticket_number}</Link></td><td>{ticketTitle(item.subject)}</td><td>{item.actor_name}</td><td>{outcomeNames[item.outcome] ?? item.outcome}</td><td>{formatDate(item.occurred_at)}</td></tr>)}</tbody></table></div>{data.details.total === 0 && <p className="muted">За выбранный период звонков нет.</p>}<div className="reports-pagination"><Button variant="quiet" disabled={page <= 1} onClick={() => setPage(page - 1)}>Назад</Button><span>{page} / {Math.max(1, Math.ceil(data.details.total / 20))}</span><Button variant="quiet" disabled={page * 20 >= data.details.total} onClick={() => setPage(page + 1)}>Далее</Button></div></section>
  </>
}

function Pagination({ page, total, setPage }: { page: number; total: number; setPage: (page: number) => void }) {
  return <div className="reports-pagination"><Button variant="quiet" disabled={page <= 1} onClick={() => setPage(page - 1)}>Назад</Button><span>{page} / {Math.max(1, Math.ceil(total / 20))}</span><Button variant="quiet" disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>Далее</Button></div>
}

const directionNames: Record<string, string> = { SUKHUM: 'Сухум', OCHAMCHIRA: 'Очамчира', UNKNOWN: 'Направление не указано' }

function FieldWorkContent({ data, page, setPage }: { data: FieldWorkReport; page: number; setPage: (page: number) => void }) {
  const m = data.metrics
  return <>
    <div className="reports-metrics"><article><span>Отправлены задания</span><strong>{m.sent_handoffs}</strong></article><article><span>Уникальные заявки</span><strong>{m.sent_unique_tickets}</strong></article><article><span>Получены отчёты</span><strong>{m.reported_handoffs}</strong></article><article><span>Проверены инженером</span><strong>{m.verified_handoffs}</strong></article><article><span>События переноса</span><strong>{m.reschedule_events}</strong><small>{m.rescheduled_handoffs} передач</small></article><article><span>Ожидают отчёта сейчас</span><strong>{m.awaiting_report_now}</strong><small>Независимо от периода</small></article><article><span>Дата не согласована сейчас</span><strong>{m.date_pending_now}</strong><small>Независимо от периода</small></article></div>
    <p className="reports-explanation">Отправка, отчёт, проверка и каждый перенос учитываются по времени соответствующего события (UTC+3). Отправленное задание не считается выполненным. Текущие ожидания не ограничены выбранным периодом.</p>
    <div className="reports-distributions reports-distributions--two"><section className="reports-panel"><h2>Направления</h2><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Направление</th><th>Отправлены</th><th>Отчёты</th><th>Проверены</th><th>Переносы</th></tr></thead><tbody>{Object.entries(data.directions).map(([key, row]) => <tr key={key}><td>{directionNames[key] ?? key}</td><td>{row.sent}</td><td>{row.reported}</td><td>{row.verified}</td><td>{row.reschedules}</td></tr>)}</tbody></table></div>{Object.keys(data.directions).length === 0 && <p className="muted">За период событий нет.</p>}</section><section className="reports-panel"><h2>Причины переносов</h2><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Причина</th><th>События</th></tr></thead><tbody>{data.reschedule_reasons.map((row) => <tr key={row.reason}><td>{row.reason}</td><td>{row.count}</td></tr>)}</tbody></table></div>{data.reschedule_reasons.length === 0 && <p className="muted">За период переносов нет.</p>}</section></div>
    <section className="reports-panel"><h2>Динамика по датам</h2><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Дата (UTC+3)</th><th>Отправлены</th><th>Отчёты</th><th>Проверены</th><th>Переносы</th></tr></thead><tbody>{data.daily.map((row) => <tr key={row.date}><td>{row.date}</td><td>{row.sent}</td><td>{row.reported}</td><td>{row.verified}</td><td>{row.reschedules}</td></tr>)}</tbody></table></div>{data.daily.length === 0 && <p className="muted">За период событий нет.</p>}</section>
    <section className="reports-panel"><div className="reports-panel__heading"><h2>Выездные задания с событиями за период</h2><span>{data.details.total}</span></div><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Заявка</th><th>Тема</th><th>Направление</th><th>Состояние</th><th>Отправлена</th><th>Отчёт</th><th>Проверена</th></tr></thead><tbody>{data.details.items.map((row) => <tr key={row.id}><td><Link to={`/tickets/${row.ticket_id}`}>#{row.ticket_number}</Link></td><td>{ticketTitle(row.subject)}</td><td>{directionNames[row.direction ?? 'UNKNOWN'] ?? row.direction}</td><td>{row.work_status === 'VERIFIED' ? 'Проверена' : row.work_status === 'REPORTED' ? 'Ожидает проверки' : row.schedule_status === 'DATE_PENDING' ? 'Перенесена, дата не согласована' : 'Ожидает отчёта'}</td><td>{row.sent_at ? formatDate(row.sent_at) : '—'}</td><td>{row.reported_at ? formatDate(row.reported_at) : '—'}</td><td>{row.verified_at ? formatDate(row.verified_at) : '—'}</td></tr>)}</tbody></table></div>{data.details.total === 0 && <p className="muted">За период работ нет.</p>}<Pagination page={page} total={data.details.total} setPage={setPage} /></section>
  </>
}

function ReasonsContent({ data, page, setPage, detailKind, setDetailKind }: { data: ReasonsReport; page: number; setPage: (page: number) => void; detailKind: 'category' | 'decision'; setDetailKind: (kind: 'category' | 'decision') => void }) {
  const m = data.metrics
  return <>
    <div className="reports-metrics"><article><span>Созданные заявки</span><strong>{m.created_tickets}</strong></article><article><span>Без категории</span><strong>{m.without_category}</strong></article><article><span>Решения инженеров</span><strong>{m.decision_events}</strong></article><article><span>Заявки с решениями</span><strong>{m.decision_unique_tickets}</strong></article><article><span>Решения без причины</span><strong>{m.decisions_without_reason}</strong></article><article><span>Созданные, без установленной причины сейчас</span><strong>{m.created_without_reason_now}</strong></article></div>
    <p className="reports-explanation">Категория — историческое название при регистрации, дата создания заявки. Причина — название в версии решения инженера, дата решения. Повторные решения считаются отдельно. Последний показатель отражает текущее состояние созданных за период заявок.</p>
    <div className="reports-distributions reports-distributions--two"><Breakdown title="Категории при регистрации" items={data.categories} missing="Без категории" /><Breakdown title="Причины решений" items={data.reasons} missing="Без причины" /></div>
    <section className="reports-panel"><h2>Обращения по категориям и датам</h2><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Дата (UTC+3)</th><th>Категория</th><th>Заявки</th></tr></thead><tbody>{data.daily.map((row, index) => <tr key={`${row.date}-${row.category}-${index}`}><td>{row.date}</td><td>{row.category ?? 'Без категории'}</td><td>{row.count}</td></tr>)}</tbody></table></div>{data.daily.length === 0 && <p className="muted">За период заявок нет.</p>}</section>
    <section className="reports-panel"><div className="reports-panel__heading"><h2>Детализация</h2><label>События <select value={detailKind} onChange={(event) => { setDetailKind(event.target.value as 'category' | 'decision'); setPage(1) }}><option value="category">Регистрация</option><option value="decision">Решения</option></select></label></div><div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Заявка</th><th>Тема</th><th>{detailKind === 'category' ? 'Категория' : 'Причина'}</th><th>Когда</th></tr></thead><tbody>{data.details.items.map((row) => <tr key={row.id}><td><Link to={`/tickets/${row.ticket_id}`}>#{row.ticket_number}</Link></td><td>{ticketTitle(row.subject)}</td><td>{row.name ?? 'Не указана'}</td><td>{formatDate(row.occurred_at)}</td></tr>)}</tbody></table></div>{data.details.total === 0 && <p className="muted">За период событий нет.</p>}<Pagination page={page} total={data.details.total} setPage={setPage} /></section>
  </>
}

export function ReportsPage() {
  const [tab, setTab] = useState<ReportTab>('general')
  const [preset, setPreset] = useState<Preset>('week')
  const [range, setRange] = useState(() => periodFor('week'))
  const [page, setPage] = useState(1)
  const [direction, setDirection] = useState('')
  const [category, setCategory] = useState('')
  const [categoryInput, setCategoryInput] = useState('')
  const [detailKind, setDetailKind] = useState<'category' | 'decision'>('category')
  const valid = !!range.start && !!range.end && range.start <= range.end && (Date.parse(range.end) - Date.parse(range.start)) / 86_400_000 <= 3660
  const report = useQuery({ queryKey: ['reports', 'general', range, page], queryFn: ({ signal }) => getGeneralReport(range.start, range.end, page, signal), enabled: valid && tab === 'general' })
  const employees = useQuery({ queryKey: ['reports', 'employees', range], queryFn: ({ signal }) => getEmployeesReport(range.start, range.end, signal), enabled: valid && tab === 'employees' })
  const callbacks = useQuery({ queryKey: ['reports', 'callbacks', range, page], queryFn: ({ signal }) => getCallbacksReport(range.start, range.end, page, signal), enabled: valid && tab === 'callbacks' })
  const fieldWork = useQuery({ queryKey: ['reports', 'field-work', range, direction, page], queryFn: ({ signal }) => getFieldWorkReport(range.start, range.end, direction, page, signal), enabled: valid && tab === 'field-work' })
  const reasons = useQuery({ queryKey: ['reports', 'reasons', range, category, detailKind, page], queryFn: ({ signal }) => getReasonsReport(range.start, range.end, category, detailKind, page, signal), enabled: valid && tab === 'reasons' })
  const active = tab === 'general' ? report : tab === 'employees' ? employees : tab === 'callbacks' ? callbacks : tab === 'field-work' ? fieldWork : reasons
  function choose(value: Preset) { setPreset(value); setPage(1); if (value !== 'custom') setRange(periodFor(value)) }
  return <div className="reports-page">
    <div className="page-heading"><div><p className="eyebrow">SERVICE DESK</p><h1>Отчёты</h1></div><BarChart3 size={25} aria-hidden="true" /></div>
    <section className="reports-filters" aria-label="Период отчёта"><div className="reports-presets">{(Object.keys(labels) as Preset[]).map((value) => <button type="button" key={value} className={preset === value ? 'reports-preset reports-preset--active' : 'reports-preset'} onClick={() => choose(value)}>{labels[value]}</button>)}</div>{preset === 'custom' && <div className="reports-range"><label>С <input type="date" value={range.start} onChange={(event) => { setRange({ ...range, start: event.target.value }); setPage(1) }} /></label><label>По <input type="date" value={range.end} onChange={(event) => { setRange({ ...range, end: event.target.value }); setPage(1) }} /></label></div>}<small>Календарные даты UTC+3. Показатели действий — по фактическому времени события.</small></section>
    <nav className="reports-presets" aria-label="Виды отчётов">{([['general', 'Общая статистика'], ['employees', 'Сотрудники'], ['callbacks', 'Контрольные звонки'], ['field-work', 'Монтажные работы'], ['reasons', 'Причины обращений']] as const).map(([key, label]) => <button type="button" key={key} className={tab === key ? 'reports-preset reports-preset--active' : 'reports-preset'} onClick={() => { setTab(key); setPage(1) }}>{label}</button>)}</nav>
    {tab === 'field-work' && <div className="reports-range"><label>Направление <select value={direction} onChange={(event) => { setDirection(event.target.value); setPage(1) }}><option value="">Все направления</option><option value="SUKHUM">Сухум</option><option value="OCHAMCHIRA">Очамчира</option></select></label></div>}
    {tab === 'reasons' && <form className="reports-range" onSubmit={(event) => { event.preventDefault(); setCategory(categoryInput.trim()); setPage(1) }}><label>Категория <input type="text" value={categoryInput} onChange={(event) => setCategoryInput(event.target.value)} placeholder="Точное историческое название" maxLength={160} /></label><Button type="submit">Применить</Button></form>}
    {!valid && <Notice tone="error">Выберите корректный период не длиннее 10 лет.</Notice>}
    {active.isPending && valid && <p role="status">Загружаем отчёт…</p>}
    {active.isError && <Notice tone="error">Не удалось получить отчёт. {errorText(active.error)}</Notice>}
    {tab === 'employees' && employees.data && <EmployeesContent data={employees.data} />}
    {tab === 'callbacks' && callbacks.data && <CallbacksContent data={callbacks.data} page={page} setPage={setPage} />}
    {tab === 'field-work' && fieldWork.data && <FieldWorkContent data={fieldWork.data} page={page} setPage={setPage} />}
    {tab === 'reasons' && reasons.data && <ReasonsContent data={reasons.data} page={page} setPage={setPage} detailKind={detailKind} setDetailKind={setDetailKind} />}
    {tab === 'general' && report.data && <>
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
