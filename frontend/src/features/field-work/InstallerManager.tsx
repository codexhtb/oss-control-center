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
type AccessRequest = { id: string; telegram_user_id: number; telegram_name: string; status: 'PENDING' | 'APPROVED' | 'REJECTED'; created_at: string }
type Employee = { id: string; display_name: string; role: string; is_active: boolean }

export function InstallerManager() {
  const queryClient = useQueryClient()
  const installers = useQuery({ queryKey: ['field-work-installers'], queryFn: () =>
    apiRequest<{ items: Installer[] }>('/api/v1/field-work/installers') })
  const requests = useQuery({ queryKey: ['field-work-access-requests'], queryFn: () =>
    apiRequest<{ items: AccessRequest[] }>('/api/v1/field-work/access-requests?status=PENDING') })
  const employees = useQuery({ queryKey: ['field-work-eligible-employees'], queryFn: () =>
    apiRequest<{ items: Employee[] }>('/api/v1/users?page=1&size=100') })
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
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleteBlocked, setDeleteBlocked] = useState(false)
  const [convertUserId, setConvertUserId] = useState('')
  const [confirmConvert, setConfirmConvert] = useState(false)
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
    setConfirmDisable(false); setConfirmDelete(false); setDeleteBlocked(false); setConvertUserId(''); setConfirmConvert(false); setError(null); setMessage('')
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

  async function remove() {
    if (!selected || busy) return
    setBusy(true); setError(null); setMessage(''); setDeleteBlocked(false)
    try {
      await apiRequest(`/api/v1/field-work/installers/${selected.id}`, { method: 'DELETE' })
      await queryClient.invalidateQueries({ queryKey: ['field-work-installers'] })
      edit(); setMessage('Монтажник удалён. Telegram User ID освобождён.')
    } catch (cause) {
      setError(cause)
      if (typeof cause === 'object' && cause !== null && 'code' in cause && cause.code === 'installer_has_history') setDeleteBlocked(true)
    } finally { setBusy(false) }
  }

  async function convertToStaff() {
    if (!selected || !convertUserId || busy || !confirmConvert) return
    setBusy(true); setError(null)
    try {
      await apiRequest(`/api/v1/field-work/installers/${selected.id}/convert-to-staff`, { method: 'POST', json: {
        target_user_id: convertUserId, expected_updated_at: selected.updated_at,
      } })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['field-work-installers'] }),
        queryClient.invalidateQueries({ queryKey: ['field-work-staff-access', convertUserId] }),
      ])
      edit(); setMessage('Монтажник отключён. Сотрудник может подтвердить Telegram одноразовым кодом.')
    } catch (cause) { setError(cause) }
    finally { setBusy(false) }
  }

  async function decide(request: AccessRequest, approve: boolean, approvedDirections: Direction[]) {
    if (busy) return
    setBusy(true); setError(null)
    try {
      await apiRequest(`/api/v1/field-work/access-requests/${request.id}/decision`, { method: 'POST', json: {
        approve, directions: approve ? approvedDirections : [],
      } })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['field-work-access-requests'] }),
        queryClient.invalidateQueries({ queryKey: ['field-work-installers'] }),
      ])
      setMessage(approve ? 'Доступ монтажнику предоставлен.' : 'Запрос отклонён.')
    } catch (cause) { setError(cause) }
    finally { setBusy(false) }
  }

  return <section className="detail-panel" aria-label="Монтажники OSS Field Work">
    <div className="detail-panel__heading"><div><h2>Монтажники OSS Field Work</h2><p className="muted">Доступ через Telegram, без отдельного логина OCC.</p></div><Button onClick={() => edit(undefined, true)}>Добавить монтажника</Button></div>
    {message && <Notice>{message}</Notice>}
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
    <section className="employee-editor__form"><h3>Запросы доступа</h3>
      {requests.isPending ? <p role="status">Загружаем запросы…</p> : requests.data?.items?.length ? requests.data.items.map((item) => <div className="employee-editor__access" key={item.id}>
        <div><strong>{item.telegram_name}</strong><span>Telegram ID {item.telegram_user_id} · {new Date(item.created_at).toLocaleString('ru-RU')}</span></div>
        <div className="user-actions"><Button disabled={busy} onClick={() => void decide(item, true, ['SUKHUM'])}>Одобрить: Сухум</Button><Button disabled={busy} variant="quiet" onClick={() => void decide(item, true, ['OCHAMCHIRA'])}>Одобрить: Очамчира</Button><Button disabled={busy} variant="quiet" onClick={() => void decide(item, true, ['SUKHUM', 'OCHAMCHIRA'])}>Оба направления</Button><Button disabled={busy} variant="quiet" onClick={() => void decide(item, false, [])}>Отклонить</Button></div>
      </div>) : <p className="muted">Новых запросов нет.</p>}
    </section>
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
      {selected && <div className="employee-editor__access">
        <div><strong>Удалить монтажника</strong><span>Удаление возможно только без отчётов и истории выполнения. Иначе отключите доступ.</span></div>
        {confirmDelete ? <div className="user-actions"><Button type="button" variant="quiet" disabled={busy} onClick={() => setConfirmDelete(false)}>Отмена</Button><Button type="button" className="button--danger" disabled={busy} onClick={() => void remove()}>{busy ? 'Удаляем…' : 'Подтвердить удаление'}</Button></div> : <Button type="button" variant="quiet" disabled={busy} onClick={() => { setConfirmDelete(true); setDeleteBlocked(false) }}>Удалить монтажника</Button>}
      </div>}
      {deleteBlocked && <Notice tone="error">Исторические данные защищены. Снимите флажок «Активен» и сохраните монтажника, чтобы отключить ему доступ.</Notice>}
      {selected && <div className="employee-editor__access"><div><strong>Перевести в сотрудника OCC</strong><span>История монтажника сохранится, активная Telegram-идентичность будет освобождена для подтверждённой привязки сотрудника.</span></div><label className="field"><span>Сотрудник</span><select value={convertUserId} onChange={(event) => { setConvertUserId(event.target.value); setConfirmConvert(false) }}><option value="">Выберите сотрудника</option>{employees.data?.items?.filter((item) => item.is_active && ['engineer', 'admin'].includes(item.role)).map((item) => <option value={item.id} key={item.id}>{item.display_name}</option>)}</select></label>{confirmConvert ? <div className="user-actions"><Button type="button" variant="quiet" onClick={() => setConfirmConvert(false)}>Отмена</Button><Button type="button" disabled={busy} onClick={() => void convertToStaff()}>Подтвердить перевод</Button></div> : <Button type="button" variant="quiet" disabled={!convertUserId || busy} onClick={() => setConfirmConvert(true)}>Перевести</Button>}</div>}
    </form>}
  </section>
}
