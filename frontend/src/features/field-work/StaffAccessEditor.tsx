import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { apiRequest, errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import type { Direction } from './miniapp-api'

type Access = { user_id: string; telegram_user_id: number | null; is_enabled: boolean; directions: Direction[]; updated_at: string | null }

export function StaffAccessEditor({ userId, readOnly = false }: { userId: string; readOnly?: boolean }) {
  const queryClient = useQueryClient()
  const access = useQuery({ queryKey: ['field-work-staff-access', userId], queryFn: () =>
    apiRequest<Access>(`/api/v1/field-work/staff-access/${userId}`) })
  const [enabled, setEnabled] = useState(false)
  const [directions, setDirections] = useState<Direction[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [message, setMessage] = useState('')
  const [confirmUnlink, setConfirmUnlink] = useState(false)
  useEffect(() => { if (access.data) { setEnabled(access.data.is_enabled); setDirections(access.data.directions) } }, [access.data])
  const toggle = (direction: Direction) => setDirections((current) => current.includes(direction)
    ? current.filter((item) => item !== direction) : [...current, direction])
  async function save() {
    if (busy || (enabled && !directions.length)) return
    setBusy(true); setError(null); setMessage('')
    try {
      await apiRequest(`/api/v1/field-work/staff-access/${userId}`, { method: 'PUT', json: {
        is_enabled: enabled, directions, expected_updated_at: access.data?.updated_at ?? null,
      } })
      await queryClient.invalidateQueries({ queryKey: ['field-work-staff-access', userId] })
      setMessage('Доступ Field Work сохранён.')
    } catch (cause) { setError(cause) }
    finally { setBusy(false) }
  }
  async function unlink() {
    if (!confirmUnlink || busy) return
    setBusy(true); setError(null)
    try {
      await apiRequest(`/api/v1/field-work/staff-access/${userId}/unlink`, { method: 'POST' })
      await queryClient.invalidateQueries({ queryKey: ['field-work-staff-access', userId] })
      setConfirmUnlink(false); setMessage('Привязка Telegram отозвана.')
    } catch (cause) { setError(cause) }
    finally { setBusy(false) }
  }
  return <section className="employee-editor__form" aria-label="Доступ сотрудника к Field Work">
    <h3>OSS Field Work</h3>
    {access.isPending ? <p role="status">Загружаем доступ…</p> : access.isError ? <Notice tone="error">{errorText(access.error)}</Notice> : <>
      <p>Telegram ID: {access.data?.telegram_user_id ?? 'не привязан'}</p>
      <p className="muted">Сотрудник подтверждает свой Telegram через одноразовый код в профиле OCC.</p>
      {readOnly && <p className="muted">Свой доступ изменяет другой администратор.</p>}
      <label><input type="checkbox" disabled={readOnly} checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> Доступ включён</label>
      <div className="user-actions"><label><input type="checkbox" disabled={readOnly} checked={directions.includes('SUKHUM')} onChange={() => toggle('SUKHUM')} /> Сухум</label><label><input type="checkbox" disabled={readOnly} checked={directions.includes('OCHAMCHIRA')} onChange={() => toggle('OCHAMCHIRA')} /> Очамчира</label></div>
      {!readOnly && <div className="user-actions"><Button disabled={busy || (enabled && !directions.length)} onClick={() => void save()}>Сохранить доступ</Button>{access.data?.telegram_user_id && <Button variant="quiet" disabled={busy} onClick={() => confirmUnlink ? void unlink() : setConfirmUnlink(true)}>{confirmUnlink ? 'Подтвердить отвязку' : 'Отвязать Telegram'}</Button>}</div>}
      {confirmUnlink && <Button variant="quiet" onClick={() => setConfirmUnlink(false)}>Отмена</Button>}
    </>}
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}{message && <Notice>{message}</Notice>}
  </section>
}
