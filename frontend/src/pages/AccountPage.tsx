import { Fingerprint } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import type { MeResponse } from '../shared/api/types'
import { Panel } from '../shared/ui/Panel'
import { roleLabel } from '../layout/AppShell'
import { authApi } from '../features/auth/auth-api'
import { apiRequest, errorText } from '../shared/api/client'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { PasswordInput } from '../shared/ui/PasswordInput'
import { EffectsToggle } from '../shared/ui/EffectsToggle'

export function AccountPage({ session, onPasswordChanged }: { session: MeResponse; onPasswordChanged: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [fieldCode, setFieldCode] = useState('')
  const [fieldError, setFieldError] = useState<unknown>(null)
  const [fieldBusy, setFieldBusy] = useState(false)

  async function requestFieldCode() {
    setFieldBusy(true); setFieldError(null); setFieldCode('')
    try {
      const result = await apiRequest<{ code: string }>('/api/v1/field-work/staff-link-code', { method: 'POST' })
      setFieldCode(result.code)
    } catch (cause) { setFieldError(cause) }
    finally { setFieldBusy(false) }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !current || !next || next !== repeat) return
    setBusy(true); setError(null)
    try {
      await authApi.changePassword(current, next)
      setCurrent(''); setNext(''); setRepeat('')
      onPasswordChanged()
    } catch (reason) { setError(reason) }
    finally { setBusy(false) }
  }

  return (
    <div className="page page--account">
      <div className="page-heading"><div><p className="eyebrow">УЧЁТНАЯ ЗАПИСЬ</p><h1>Мой профиль</h1></div></div>
      <div className="account-grid">
        <Panel className="identity-panel">
          <div className="panel-icon panel-icon--accent"><Fingerprint size={22} /></div>
          <p className="eyebrow">УЧЁТНАЯ ЗАПИСЬ</p>
          <h2>{session.user.display_name}</h2>
          <dl className="detail-list"><div><dt>Логин</dt><dd>{session.user.login}</dd></div><div><dt>Роль</dt><dd>{roleLabel(session.user.role)}</dd></div><div><dt>Статус</dt><dd>{session.user.is_active ? 'Активна' : 'Неактивна'}</dd></div></dl>
        </Panel>
        <Panel className="account-password-panel">
        <h2>Изменить пароль</h2>
        <p className="muted">После изменения все активные сеансы будут завершены.</p>
        <form className="inline-form account-password-form" onSubmit={(event) => void changePassword(event)}>
          <PasswordInput label="Текущий пароль" name="current-password" autoComplete="current-password" value={current} onChange={setCurrent} />
          <PasswordInput label="Новый пароль" name="new-password" value={next} onChange={setNext} />
          <PasswordInput label="Подтвердите новый пароль" name="repeat-password" value={repeat} onChange={setRepeat} />
          {next && repeat && next !== repeat && <Notice tone="error">Пароли не совпадают.</Notice>}
          {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
          <Button type="submit" disabled={busy || !current || !next || next !== repeat}>{busy ? 'Сохраняем…' : 'Изменить пароль'}</Button>
        </form>
      </Panel>
      </div>
      <Panel className="account-effects"><h2>Отображение</h2><EffectsToggle /></Panel>
      {session.user.role !== 'operator' && <Panel className="account-effects"><h2>OSS Field Work в Telegram</h2><p className="muted">Администратор сначала включает доступ и направления в карточке сотрудника. Затем получите код и введите его в Mini App. Код действует 10 минут и используется один раз.</p><Button disabled={fieldBusy} onClick={() => void requestFieldCode()}>{fieldBusy ? 'Создаём код…' : 'Получить код привязки'}</Button>{fieldError !== null && <Notice tone="error">{errorText(fieldError)}</Notice>}{fieldCode && <p role="status">Код для Mini App: <code>{fieldCode}</code></p>}</Panel>}
    </div>
  )
}
