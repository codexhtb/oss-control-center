import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type FormEvent } from 'react'
import { apiRequest, errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import type { TicketCategory } from './types'

function CategoryRow({ item, reload }: { item: TicketCategory; reload: () => Promise<void> }) {
  const [name, setName] = useState(item.name)
  const [order, setOrder] = useState(item.sort_order)
  const [active, setActive] = useState(item.is_active)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => { setName(item.name); setOrder(item.sort_order); setActive(item.is_active) }, [item.version, item.name, item.sort_order, item.is_active])
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setBusy(true); setError(null)
    try {
      await apiRequest<TicketCategory>(`/api/v1/ticket-categories/${encodeURIComponent(item.id)}`, {
        method: 'PATCH', ifMatch: `"${item.version}"`, json: { name: name.trim(), sort_order: order, is_active: active },
      })
      await reload()
    } catch (reason) { setError(reason) }
    finally { setBusy(false) }
  }
  return <form className="category-editor-row" onSubmit={(event) => void save(event)}>
    <label className="field"><span>Название</span><input aria-label={`Название категории ${item.name}`} value={name} maxLength={160} required onChange={(event) => setName(event.target.value)} /></label>
    <label className="field"><span>Порядок</span><input aria-label={`Порядок категории ${item.name}`} type="number" min={0} max={100000} value={order} onChange={(event) => setOrder(Number(event.target.value))} /></label>
    <label className="category-editor-row__active"><input type="checkbox" checked={active} onChange={(event) => setActive(event.target.checked)} /> Активна</label>
    <Button type="submit" disabled={busy || !name.trim() || (name === item.name && order === item.sort_order && active === item.is_active)}>{busy ? 'Сохраняем…' : 'Сохранить'}</Button>
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
  </form>
}

export function CategoryManager() {
  const client = useQueryClient()
  const categories = useQuery({ queryKey: ['ticket-categories', 'all'], queryFn: ({ signal }) => apiRequest<TicketCategory[]>('/api/v1/ticket-categories?include_inactive=true', { signal }) })
  const [name, setName] = useState('')
  const [order, setOrder] = useState(90)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  async function reload() { await client.invalidateQueries({ queryKey: ['ticket-categories'] }) }
  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setBusy(true); setError(null)
    try {
      await apiRequest('/api/v1/ticket-categories', { method: 'POST', json: { name: name.trim(), sort_order: order } })
      setName('')
      await reload()
    } catch (reason) { setError(reason) }
    finally { setBusy(false) }
  }
  return <section className="detail-panel"><div className="detail-panel__heading"><h2>Темы обращений</h2></div>
    {categories.isPending ? <p role="status">Загружаем темы…</p> : categories.isError ? <Notice tone="error">{errorText(categories.error)}</Notice> : <div className="category-editor">{categories.data.map((item) => <CategoryRow key={item.id} item={item} reload={reload} />)}</div>}
    <form className="category-editor-row" onSubmit={(event) => void add(event)}>
      <label className="field"><span>Новая тема</span><input aria-label="Новая тема" value={name} maxLength={160} required onChange={(event) => setName(event.target.value)} /></label>
      <label className="field"><span>Порядок</span><input aria-label="Порядок новой темы" type="number" min={0} max={100000} value={order} onChange={(event) => setOrder(Number(event.target.value))} /></label>
      <Button type="submit" disabled={busy || !name.trim()}>{busy ? 'Добавляем…' : 'Добавить тему'}</Button>
    </form>
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
  </section>
}
