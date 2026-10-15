import { useState, type FormEvent } from 'react'
import { authApi } from './auth-api'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import { errorText } from '../../shared/api/client'

export function PasswordChangePage({ onChanged }: { onChanged: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || next.length < 12 || next !== repeat) return
    setBusy(true)
    setError(null)
    try {
      await authApi.changePassword(current, next)
      setCurrent(''); setNext(''); setRepeat('')
      onChanged()
    } catch (reason) {
      setError(reason)
    } finally {
      setBusy(false)
    }
  }

  return <main className="login-layout"><div className="login-card" style={{ margin: 'auto' }}>
    <h1>Смена временного пароля</h1>
    <p className="muted">Задайте собственный пароль. После сохранения войдите снова.</p>
    <form onSubmit={(event) => void submit(event)}>
      <label className="field"><span>Временный пароль</span><input type="password" name="current-password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} required /></label>
      <label className="field"><span>Новый пароль</span><input type="password" name="new-password" autoComplete="new-password" value={next} onChange={(event) => setNext(event.target.value)} minLength={12} required /></label>
      <label className="field"><span>Повторите пароль</span><input type="password" name="repeat-password" autoComplete="new-password" value={repeat} onChange={(event) => setRepeat(event.target.value)} required /></label>
      {next && repeat && next !== repeat && <Notice tone="error">Пароли не совпадают.</Notice>}
      {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
      <Button type="submit" variant="primary" disabled={busy || !current || next.length < 12 || next !== repeat}>{busy ? 'Сохраняем…' : 'Сменить пароль'}</Button>
    </form>
  </div></main>
}
