import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { apiRequest, errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import type { Direction } from './miniapp-api'

type Installer = {
  id: string; telegram_user_id: number; name: string; is_active: boolean
  directions: Direction[]; updated_at: string
}

export function InstallerManager() {
  const queryClient = useQueryClient()
  const installers = useQuery({ queryKey: ['field-work-installers'], queryFn: () =>
    apiRequest<{ items: Installer[] }>('/api/v1/field-work/installers') })
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [telegramId, setTelegramId] = useState('')
  const [directions, setDirections] = useState<Direction[]>([])
  const [active, setActive] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [message, setMessage] = useState('')
  const [confirmDisable, setConfirmDisable] = useState(false)
  const selected = installers.data?.items.find((item) => item.id === selectedId)
  const filtered = installers.data?.items.filter((item) =>
    `${item.name} ${item.telegram_user_id}`.toLocaleLowerCase('ru').includes(search.trim().toLocaleLowerCase('ru'))) ?? []

  function edit(item?: Installer, openCreate = false) {
    setCreating(openCreate)
    setSelectedId(item?.id ?? null)
    setName(item?.name ?? '')
    setTelegramId(item ? String(item.telegram_user_id) : '')
    setDirections(item?.directions ?? [])
    setActive(item?.is_active ?? true)
    setConfirmDisable(false); setError(null); setMessage('')
  }

  function toggle(direction: Direction) {
    setDirections((current) => current.includes(direction)
      ? current.filter((item) => item !== direction) : [...current, direction])
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || !name.trim() || !/^\d+$/.test(telegramId) || !directions.length) return
    if (selected?.is_active && !active && !confirmDisable) { setConfirmDisable(true); return }
    setBusy(true); setError(null); setMessage('')
    try {
      const payload = {
        telegram_user_id: Number(telegramId), name: name.trim(), is_active: active, directions,
        ...(selected ? { expected_updated_at: selected.updated_at } : {}),
      }
      const result = await apiRequest<Installer>(selected
        ? `/api/v1/field-work/installers/${selected.id}` : '/api/v1/field-work/installers',
      { method: selected ? 'PUT' : 'POST', json: payload })
      await queryClient.invalidateQueries({ queryKey: ['field-work-installers'] })
      edit(result); setMessage(selected ? 'Монтажник обновлён.' : 'Монтажник добавлен.')
    } catch (cause) { setError(cause) }
    finally { setBusy(false) }
  }

  return <section className="detail-panel" aria-label="Монтажники OSS Field Work">
    <div className="detail-panel__heading"><div><h2>Монтажники OSS Field Work</h2><p className="muted">Доступ через Telegram, без отдельного логина OCC.</p></div><Button onClick={() => edit(undefined, true)}>Добавить монтажника</Button></div>
    {message && <Notice>{message}</Notice>}
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
    <label className="field"><span>Поиск по имени или Telegram ID</span><input value={search} onChange={(event) => setSearch(event.target.value)} /></label>
    {installers.isPending ? <p role="status">Загружаем монтажников…</p> : installers.isError ? <Notice tone="error">{errorText(installers.error)}</Notice> :
      <div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Имя</th><th>Telegram ID</th><th>Направления</th><th>Статус</th><th /></tr></thead><tbody>
        {filtered.map((item) => <tr key={item.id}><td>{item.name}</td><td>{item.telegram_user_id}</td><td>{item.directions.map((direction) => direction === 'SUKHUM' ? 'Сухум' : 'Очамчира').join(', ')}</td><td>{item.is_active ? 'Активен' : 'Отключён'}</td><td><Button variant="quiet" onClick={() => edit(item)}>Изменить</Button></td></tr>)}
      </tbody></table>{!filtered.length && <p className="muted">Монтажники не найдены.</p>}</div>}
    {(selectedId || creating) && <form className="employee-editor__form" onSubmit={(event) => void submit(event)}>
      <h3>{selected ? 'Изменить монтажника' : 'Новый монтажник'}</h3>
      <div className="employee-editor__fields"><label className="field"><span>Имя</span><input value={name} onChange={(event) => setName(event.target.value)} maxLength={160} required /></label><label className="field"><span>Telegram User ID</span><input inputMode="numeric" pattern="[0-9]+" value={telegramId} onChange={(event) => setTelegramId(event.target.value)} required /></label></div>
      <div className="user-actions"><label><input type="checkbox" checked={directions.includes('SUKHUM')} onChange={() => toggle('SUKHUM')} /> Сухум</label><label><input type="checkbox" checked={directions.includes('OCHAMCHIRA')} onChange={() => toggle('OCHAMCHIRA')} /> Очамчира</label><label><input type="checkbox" checked={active} onChange={(event) => { setActive(event.target.checked); setConfirmDisable(false) }} /> Активен</label></div>
      {confirmDisable && <Notice tone="error">Подтвердите отключение монтажника повторным нажатием «Сохранить».</Notice>}
      <div className="user-actions"><Button type="submit" disabled={busy || !directions.length || !name.trim() || !/^\d+$/.test(telegramId)}>{busy ? 'Сохраняем…' : 'Сохранить'}</Button><Button type="button" variant="quiet" onClick={() => edit()}>Отмена</Button></div>
    </form>}
  </section>
}
