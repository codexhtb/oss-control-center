import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, CalendarDays, Check, ChevronRight, Clipboard, LoaderCircle, Phone, RefreshCw } from 'lucide-react'
import { type Direction, type Handoff, type HandoffDetail, type Page, type Partner, type QueueTab, type Reschedule, fieldWorkRequest } from './miniapp-api'
import './field-work.css'

type TelegramWebApp = {
  initData: string
  colorScheme: 'light' | 'dark'
  ready: () => void
  expand: () => void
  onEvent: (event: 'themeChanged', handler: () => void) => void
  offEvent: (event: 'themeChanged', handler: () => void) => void
}

declare global { interface Window { Telegram?: { WebApp?: TelegramWebApp } } }

const tabs: { id: QueueTab; label: string }[] = [
  { id: 'open', label: 'Открытые' },
  { id: 'completed', label: 'Завершённые' },
  { id: 'history', label: 'История' },
]
const directionName: Record<Direction, string> = { SUKHUM: 'Сухум', OCHAMCHIRA: 'Очамчира' }
const formatDate = (value: string | null) => value
  ? new Intl.DateTimeFormat('ru-RU', { timeZone: 'Etc/GMT-3', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
  : 'Не указана'
const visitDate = (value: { scheduled_for: string | null; scheduled_date: string | null; schedule_status: string }) =>
  value.schedule_status === 'DATE_PENDING' ? 'Дата не согласована'
    : value.scheduled_for ? formatDate(value.scheduled_for)
    : value.scheduled_date ? new Intl.DateTimeFormat('ru-RU', { timeZone: 'Etc/GMT-3', day: '2-digit', month: 'long', year: 'numeric' }).format(new Date(`${value.scheduled_date}T12:00:00+03:00`)) + ' · время не согласовано'
    : 'Дата выезда не указана'

function sdk(): Promise<TelegramWebApp> {
  const current = window.Telegram?.WebApp
  if (current?.initData) return Promise.resolve(current)
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://telegram.org/js/telegram-web-app.js'
    script.async = true
    script.onload = () => {
      const app = window.Telegram?.WebApp
      if (app?.initData) resolve(app)
      else reject(new Error('Откройте OSS Field Work через Telegram.'))
    }
    script.onerror = () => reject(new Error('Не удалось загрузить Telegram. Повторите попытку.'))
    document.head.append(script)
  })
}

export function FieldWorkApp() {
  const [initData, setInitData] = useState('')
  const [theme, setTheme] = useState<'light' | 'dark'>('dark')
  const [authError, setAuthError] = useState('')
  const [name, setName] = useState('')
  const [directions, setDirections] = useState<Direction[]>([])
  const [direction, setDirection] = useState<Direction | 'all'>('all')
  const [tab, setTab] = useState<QueueTab>('open')
  const [page, setPage] = useState<Page<Handoff> | null>(null)
  const [items, setItems] = useState<Handoff[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<HandoffDetail | null>(null)
  const [history, setHistory] = useState<Reschedule[]>([])
  const [historyPage, setHistoryPage] = useState<Page<Reschedule> | null>(null)
  const [partners, setPartners] = useState<Partner[]>([])
  const [workText, setWorkText] = useState('')
  const [partnerId, setPartnerId] = useState('')
  const [requestId, setRequestId] = useState('')
  const [scheduleReason, setScheduleReason] = useState('')
  const [scheduleDate, setScheduleDate] = useState('')
  const [scheduleTime, setScheduleTime] = useState('')
  const [mode, setMode] = useState<'view' | 'report' | 'reschedule'>('view')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let alive = true
    let app: TelegramWebApp | undefined
    const changed = () => { if (app && alive) setTheme(app.colorScheme) }
    void sdk().then((value) => {
      if (!alive) return
      app = value
      app.ready(); app.expand(); changed()
      app.onEvent('themeChanged', changed)
      setInitData(app.initData)
    }).catch((cause: unknown) => { if (alive) setAuthError(cause instanceof Error ? cause.message : 'Ошибка Telegram') })
    return () => { alive = false; app?.offEvent('themeChanged', changed) }
  }, [])

  useEffect(() => {
    if (!initData) return
    let alive = true
    void fieldWorkRequest<{ name: string; directions: Direction[] }>(initData, '/me')
      .then((value) => { if (alive) { setName(value.name); setDirections(value.directions) } })
      .catch((cause: unknown) => { if (alive) setAuthError(cause instanceof Error ? cause.message : 'Нет доступа') })
    return () => { alive = false }
  }, [initData])

  const load = useCallback(async (nextPage = 1) => {
    if (!initData || !directions.length) return
    setLoading(true); setError('')
    try {
      const params = new URLSearchParams({ queue: tab, page: String(nextPage), page_size: '20' })
      if (direction !== 'all') params.set('direction', direction)
      const result = await fieldWorkRequest<Page<Handoff>>(initData, `/handoffs?${params}`)
      setPage(result)
      setItems((previous) => nextPage === 1 ? result.items : [...previous, ...result.items])
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Ошибка загрузки') }
    finally { setLoading(false) }
  }, [initData, directions.length, tab, direction])

  useEffect(() => { void load(1) }, [load])

  const open = async (id: string) => {
    if (!initData) return
    setError(''); setMode('view')
    try {
      const [detail, reschedules] = await Promise.all([
        fieldWorkRequest<HandoffDetail>(initData, `/handoffs/${id}`),
        fieldWorkRequest<Page<Reschedule>>(initData, `/handoffs/${id}/reschedules?page_size=20`),
      ])
      setSelected(detail); setHistory(reschedules.items); setHistoryPage(reschedules)
      if (detail.work_status === 'AWAITING_REPORT') {
        const value = await fieldWorkRequest<{ items: Partner[] }>(initData, `/partners?direction=${detail.direction}`)
        setPartners(value.items)
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Ошибка загрузки') }
  }

  const complete = async () => {
    if (!selected || !workText.trim() || saving) return
    const id = requestId || crypto.randomUUID()
    setRequestId(id); setSaving(true); setError('')
    try {
      await fieldWorkRequest(initData, `/handoffs/${selected.id}/report`, {
        client_request_id: id, partner_installer_id: partnerId || null, work_text: workText.trim(),
      })
      setSelected(null); setWorkText(''); setPartnerId(''); setRequestId(''); setMode('view')
      setTab('completed'); setItems([]); setPage(null)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сохранить отчёт') }
    finally { setSaving(false) }
  }

  const reschedule = async () => {
    if (!selected || !scheduleReason.trim() || saving) return
    setSaving(true); setError('')
    try {
      await fieldWorkRequest(initData, `/handoffs/${selected.id}/reschedules`, {
        reason: scheduleReason.trim(), expected_version: selected.schedule_version,
        scheduled_for: scheduleDate && scheduleTime ? `${scheduleDate}T${scheduleTime}:00+03:00` : null,
        scheduled_date: scheduleDate && !scheduleTime ? scheduleDate : null,
      })
      setMode('view'); setScheduleReason(''); setScheduleDate(''); setScheduleTime('')
      await open(selected.id)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось перенести выезд') }
    finally { setSaving(false) }
  }

  const copyAddress = async () => {
    if (selected?.address) {
      try { await navigator.clipboard.writeText(selected.address) }
      catch { setError('Не удалось скопировать адрес') }
    }
  }

  const moreHistory = async () => {
    if (!selected || !historyPage || history.length >= historyPage.total) return
    try {
      const next = await fieldWorkRequest<Page<Reschedule>>(
        initData, `/handoffs/${selected.id}/reschedules?page=${historyPage.page + 1}&page_size=20`,
      )
      setHistory((current) => [...current, ...next.items])
      setHistoryPage(next)
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Ошибка загрузки истории') }
  }

  return <main className="fw-app" data-theme={theme}>
    <div className="fw-shell">
      <header className="fw-header">
        <div className="fw-logo">OSS<span>●</span></div>
        <div><p className="fw-kicker">FIELD WORK</p><h1>Выездные работы</h1></div>
        {name && <span className="fw-user">{name}</span>}
      </header>
      {authError ? <div className="fw-state fw-error" role="alert">{authError}</div> : !directions.length ? <div className="fw-state" role="status"><LoaderCircle className="fw-spin" /> Проверяем доступ…</div> : <>
        {!selected ? <>
          <div className="fw-filters">
            {directions.length > 1 && <select aria-label="Направление" value={direction} onChange={(event) => setDirection(event.target.value as Direction | 'all')}>
              <option value="all">Все направления</option>
              {directions.map((item) => <option value={item} key={item}>{directionName[item]}</option>)}
            </select>}
            <button className="fw-icon" aria-label="Обновить" onClick={() => void load(1)}><RefreshCw size={18} /></button>
          </div>
          <nav className="fw-tabs" aria-label="Очереди">
            {tabs.map((item) => <button key={item.id} className={tab === item.id ? 'active' : ''} onClick={() => { setTab(item.id); setItems([]); setPage(null) }}>{item.label}</button>)}
          </nav>
          {error && <div className="fw-message fw-error" role="alert">{error}</div>}
          {loading && !page ? <div className="fw-state" role="status"><LoaderCircle className="fw-spin" /> Загружаем задания…</div> : items.length ? <section className="fw-list" aria-label="Задания">
            {items.map((item) => <button className="fw-card" key={item.id} onClick={() => void open(item.id)}>
              <span className="fw-card__top"><strong>Заявка №{item.ticket_number}</strong><span>{directionName[item.direction]}</span></span>
              <span className="fw-card__subject">{item.subject}</span>
              <span className="fw-card__bottom"><span>{tab === 'open' ? 'Передано' : tab === 'completed' ? 'Отчёт' : 'Проверено'} · {formatDate(tab === 'open' ? item.sent_at : tab === 'completed' ? item.reported_at : item.verified_at)}</span><ChevronRight size={18} /></span>
            </button>)}
            {page && items.length < page.total && <button className="fw-more" disabled={loading} onClick={() => void load(page.page + 1)}>Показать ещё</button>}
          </section> : !loading ? <div className="fw-state">Заданий пока нет</div> : null}
        </> : <>
          <button className="fw-back" onClick={() => { setSelected(null); setMode('view'); setError('') }}><ArrowLeft size={18} /> К списку</button>
          <article className="fw-detail">
            <div className="fw-detail__title"><span className="fw-badge">{directionName[selected.direction]}</span><span className="fw-badge">{selected.work_status === 'AWAITING_REPORT' ? 'Открыто' : selected.work_status === 'REPORTED' ? 'Ждёт проверки' : 'Проверено'}</span></div>
            <h2>Заявка №{selected.ticket_number}</h2>
            <p className="fw-subject">{selected.subject}</p>
            <div className="fw-facts">
              <div><span>Причина выезда</span><strong>{selected.reason}</strong></div>
              <div><span>Абонент</span><strong>{selected.name || 'Не указано'}</strong></div>
              <div><span>Телефон</span><strong>{selected.phone || 'Не указан'}</strong></div>
              <div><span>Населённый пункт</span><strong>{selected.city || 'Не указан'}</strong></div>
              <div><span>Адрес</span><strong>{selected.address || 'Не указан'}</strong></div>
              <div><span>Передано</span><strong>{formatDate(selected.sent_at)}</strong></div>
              <div><span>Дата выезда</span><strong>{visitDate(selected)}</strong></div>
              {selected.schedule_reason && <div><span>Причина переноса</span><strong>{selected.schedule_reason}</strong></div>}
            </div>
            <div className="fw-actions">
              {selected.phone && <a className="fw-action" href={`tel:${selected.phone.replace(/[^+\d]/g, '')}`}><Phone size={18} /> Позвонить</a>}
              {selected.address && <button className="fw-action" onClick={() => void copyAddress()}><Clipboard size={18} /> Копировать адрес</button>}
            </div>
            {selected.report && <div className="fw-report"><h3>Отчёт о работе</h3><p>{selected.report.text}</p><small>{selected.report.installer}{selected.report.partner ? ` · вместе с ${selected.report.partner}` : ''} · {formatDate(selected.report.reported_at)}</small></div>}
            {selected.work_status === 'AWAITING_REPORT' && mode === 'view' && <div className="fw-primary-actions"><button className="fw-primary" onClick={() => setMode('report')}><Check size={18} /> Завершить работу</button><button className="fw-secondary" onClick={() => setMode('reschedule')}><CalendarDays size={18} /> Перенести</button></div>}
            {selected.work_status === 'AWAITING_REPORT' && mode === 'report' && <div className="fw-form"><h3>Завершение работы</h3><label>Работали вместе <span>необязательно</span><select value={partnerId} onChange={(event) => { setPartnerId(event.target.value); setRequestId('') }}><option value="">Без напарника</option>{partners.map((person) => <option value={person.id} key={person.id}>{person.name}</option>)}</select></label><label>Что было сделано<textarea value={workText} maxLength={4000} rows={5} onChange={(event) => { setWorkText(event.target.value); setRequestId('') }} placeholder="Опишите выполненные работы" /></label><div className="fw-form__buttons"><button className="fw-secondary" onClick={() => setMode('view')}>Отмена</button><button className="fw-primary" disabled={saving || !workText.trim()} onClick={() => void complete()}>{saving ? 'Сохраняем…' : 'Отправить отчёт'}</button></div></div>}
            {selected.work_status === 'AWAITING_REPORT' && mode === 'reschedule' && <div className="fw-form"><h3>Перенос выезда</h3><label>Причина переноса<textarea value={scheduleReason} maxLength={2000} rows={3} onChange={(event) => setScheduleReason(event.target.value)} placeholder="Почему переносится выезд?" /></label><label>Новая дата <span>оставьте пустой, если не согласована</span><input type="date" value={scheduleDate} onChange={(event) => { setScheduleDate(event.target.value); if (!event.target.value) setScheduleTime('') }} /></label>{scheduleDate && <label>Время <span>необязательно</span><input type="time" value={scheduleTime} onChange={(event) => setScheduleTime(event.target.value)} /></label>}<div className="fw-form__buttons"><button className="fw-secondary" onClick={() => setMode('view')}>Отмена</button><button className="fw-primary" disabled={saving || !scheduleReason.trim()} onClick={() => void reschedule()}>{saving ? 'Сохраняем…' : 'Сохранить перенос'}</button></div></div>}
            {history.length > 0 && <section className="fw-history"><h3>История переносов</h3>{history.map((item) => <div key={item.id}><strong>{item.scheduled_for ? formatDate(item.scheduled_for) : item.scheduled_date || 'Дата не согласована'}</strong><span>{item.reason}</span><small>{item.author_name} · {formatDate(item.created_at)}</small></div>)}{historyPage && history.length < historyPage.total && <button className="fw-more" onClick={() => void moreHistory()}>Показать ещё</button>}</section>}
          </article>
          {error && <div className="fw-message fw-error" role="alert">{error}</div>}
        </>}
      </>}
    </div>
  </main>
}
