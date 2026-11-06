import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import { settingsApi, type DecisionReasonSetting, type TemplatePurpose, type TicketTextTemplate } from './settings-api'

const purposeNames: Record<TemplatePurpose, string> = {
  decision: 'Решение инженера', comment: 'Комментарий к заявке', callback: 'Контрольный обзвон',
}

function ReasonRow({ item, reload }: { item: DecisionReasonSetting; reload: () => Promise<void> }) {
  const [name, setName] = useState(item.name)
  const [order, setOrder] = useState(item.sort_order)
  const [active, setActive] = useState(item.is_active)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => { setName(item.name); setOrder(item.sort_order); setActive(item.is_active) }, [item])
  const changed = name !== item.name || order !== item.sort_order || active !== item.is_active
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!changed || busy) return
    setBusy(true); setError(null)
    try {
      await settingsApi.updateReason(item.code, item.version, { name: name.trim(), sort_order: order, is_active: active })
      await reload()
    } catch (reason) {
      setError(reason)
      await reload()
    } finally { setBusy(false) }
  }
  async function remove() {
    if (busy || !window.confirm(`Удалить неиспользуемую причину «${item.name}»?`)) return
    setBusy(true); setError(null)
    try {
      await settingsApi.deleteReason(item.code, item.version)
      await reload()
    } catch (reason) {
      setError(reason)
      await reload()
    } finally { setBusy(false) }
  }
  return <form className="settings-editor-row" onSubmit={(event) => void save(event)}>
    <code>{item.code}</code>
    <label className="field"><span>Название</span><input aria-label={`Название причины ${item.code}`} value={name} maxLength={160} required onChange={(event) => setName(event.target.value)} /></label>
    <label className="field"><span>Порядок</span><input aria-label={`Порядок причины ${item.code}`} type="number" min={0} max={100000} value={order} onChange={(event) => setOrder(Number(event.target.value))} /></label>
    <label className="settings-editor-active"><input type="checkbox" aria-label={`Активна причина ${item.code}`} checked={active} disabled={item.code === 'NOT_ESTABLISHED'} onChange={(event) => setActive(event.target.checked)} /> Активна</label>
    <Button type="submit" disabled={busy || !name.trim() || !changed}>Сохранить</Button>
    {!item.is_builtin && <Button type="button" variant="quiet" disabled={busy} onClick={() => void remove()}>Удалить</Button>}
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
  </form>
}

export function ReasonManager() {
  const client = useQueryClient()
  const reasons = useQuery({ queryKey: ['decision-reasons', 'all'], queryFn: ({ signal }) => settingsApi.reasons(true, signal) })
  const [name, setName] = useState('')
  const [order, setOrder] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  async function reload() { await client.invalidateQueries({ queryKey: ['decision-reasons'] }) }
  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !name.trim()) return
    setBusy(true); setError(null)
    try {
      await settingsApi.createReason({ name: name.trim(), sort_order: order })
      setName(''); setOrder(0)
      await reload()
    } catch (reason) { setError(reason) }
    finally { setBusy(false) }
  }
  return <section className="detail-panel settings-section"><div className="detail-panel__heading"><h2>Причины решений</h2></div>
    {reasons.isPending ? <p role="status">Загружаем причины…</p> : reasons.isError ? <Notice tone="error">{errorText(reasons.error)}</Notice> : reasons.data.length === 0 ? <p className="muted">Причины не найдены.</p> : <div className="settings-editor-list">{reasons.data.map((item) => <ReasonRow key={item.code} item={item} reload={reload} />)}</div>}
    <form className="settings-template-row settings-template-row--new settings-reason-create" onSubmit={(event) => void add(event)}>
      <h3>Новая причина</h3>
      <div className="settings-template-fields">
        <label className="field"><span>Название</span><input aria-label="Название новой причины" value={name} maxLength={160} required onChange={(event) => setName(event.target.value)} /></label>
        <label className="field"><span>Порядок</span><input aria-label="Порядок новой причины" type="number" min={0} max={100000} value={order} onChange={(event) => setOrder(Number(event.target.value))} /></label>
      </div>
      <Button type="submit" disabled={busy || !name.trim()}>Создать причину</Button>
      {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
    </form>
    <p className="muted">Коды причин неизменяемы. Использованные причины нельзя удалить; их прежние названия сохраняются в решениях.</p>
  </section>
}

function TemplateRow({ item, reload }: { item: TicketTextTemplate; reload: () => Promise<void> }) {
  const [name, setName] = useState(item.name)
  const [body, setBody] = useState(item.body)
  const [purpose, setPurpose] = useState(item.purpose)
  const [order, setOrder] = useState(item.sort_order)
  const [active, setActive] = useState(item.is_active)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => { setName(item.name); setBody(item.body); setPurpose(item.purpose); setOrder(item.sort_order); setActive(item.is_active) }, [item])
  const changed = name !== item.name || body !== item.body || purpose !== item.purpose || order !== item.sort_order || active !== item.is_active
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!changed || busy) return
    setBusy(true); setError(null)
    try {
      await settingsApi.updateTemplate(item.id, item.version, { name: name.trim(), body: body.trim(), purpose, sort_order: order, is_active: active })
      await reload()
    } catch (reason) {
      setError(reason)
      await reload()
    } finally { setBusy(false) }
  }
  return <form className="settings-template-row" onSubmit={(event) => void save(event)}>
    <div className="settings-template-fields">
      <label className="field"><span>Название</span><input aria-label={`Название шаблона ${item.name}`} value={name} maxLength={160} required onChange={(event) => setName(event.target.value)} /></label>
      <label className="field"><span>Назначение</span><select aria-label={`Назначение шаблона ${item.name}`} value={purpose} onChange={(event) => setPurpose(event.target.value as TemplatePurpose)}>{Object.entries(purposeNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="field"><span>Порядок</span><input aria-label={`Порядок шаблона ${item.name}`} type="number" min={0} max={100000} value={order} onChange={(event) => setOrder(Number(event.target.value))} /></label>
      <label className="settings-editor-active"><input type="checkbox" aria-label={`Активен шаблон ${item.name}`} checked={active} onChange={(event) => setActive(event.target.checked)} /> Активен</label>
    </div>
    <label className="field"><span>Текст шаблона</span><textarea aria-label={`Текст шаблона ${item.name}`} value={body} maxLength={10000} rows={3} required onChange={(event) => setBody(event.target.value)} /></label>
    <Button type="submit" disabled={busy || !name.trim() || !body.trim() || !changed}>Сохранить</Button>
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
  </form>
}

export function TemplateManager() {
  const client = useQueryClient()
  const templates = useQuery({ queryKey: ['ticket-text-templates', 'all'], queryFn: ({ signal }) => settingsApi.templates(undefined, true, signal) })
  const [name, setName] = useState('')
  const [body, setBody] = useState('')
  const [purpose, setPurpose] = useState<TemplatePurpose>('decision')
  const [order, setOrder] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  async function reload() { await client.invalidateQueries({ queryKey: ['ticket-text-templates'] }) }
  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !name.trim() || !body.trim()) return
    setBusy(true); setError(null)
    try {
      await settingsApi.createTemplate({ name: name.trim(), body: body.trim(), purpose, sort_order: order })
      setName(''); setBody('')
      await reload()
    } catch (reason) { setError(reason) }
    finally { setBusy(false) }
  }
  return <section className="detail-panel settings-section"><div className="detail-panel__heading"><h2>Текстовые шаблоны</h2></div>
    {templates.isPending ? <p role="status">Загружаем шаблоны…</p> : templates.isError ? <Notice tone="error">{errorText(templates.error)}</Notice> : templates.data.length === 0 ? <p className="muted">Шаблонов пока нет.</p> : <div className="settings-editor-list">{templates.data.map((item) => <TemplateRow key={item.id} item={item} reload={reload} />)}</div>}
    <form className="settings-template-row settings-template-row--new" onSubmit={(event) => void add(event)}>
      <h3>Новый шаблон</h3>
      <div className="settings-template-fields"><label className="field"><span>Название</span><input aria-label="Название нового шаблона" value={name} maxLength={160} required onChange={(event) => setName(event.target.value)} /></label>
        <label className="field"><span>Назначение</span><select aria-label="Назначение нового шаблона" value={purpose} onChange={(event) => setPurpose(event.target.value as TemplatePurpose)}>{Object.entries(purposeNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="field"><span>Порядок</span><input aria-label="Порядок нового шаблона" type="number" min={0} max={100000} value={order} onChange={(event) => setOrder(Number(event.target.value))} /></label></div>
      <label className="field"><span>Текст</span><textarea aria-label="Текст нового шаблона" value={body} maxLength={10000} rows={3} required onChange={(event) => setBody(event.target.value)} /></label>
      <Button type="submit" disabled={busy || !name.trim() || !body.trim()}>Создать шаблон</Button>
      {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
    </form>
  </section>
}
