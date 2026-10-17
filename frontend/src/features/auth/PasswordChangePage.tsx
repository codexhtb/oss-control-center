import { useState, type FormEvent } from 'react'
import { authApi } from './auth-api'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import { errorText } from '../../shared/api/client'
import { PasswordInput } from '../../shared/ui/PasswordInput'

export function PasswordChangePage({ onChanged }: { onChanged: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !current || !next || next !== repeat) return
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
      <PasswordInput label="Временный пароль" name="current-password" autoComplete="current-password" value={current} onChange={setCurrent} />
      <PasswordInput label="Новый пароль" name="new-password" value={next} onChange={setNext} />
      <PasswordInput label="Повторите пароль" name="repeat-password" value={repeat} onChange={setRepeat} />
      {next && repeat && next !== repeat && <Notice tone="error">Пароли не совпадают.</Notice>}
      {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
      <Button type="submit" variant="primary" disabled={busy || !current || !next || next !== repeat}>{busy ? 'Сохраняем…' : 'Сменить пароль'}</Button>
    </form>
  </div></main>
}
