import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, LockKeyhole, ShieldCheck } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { ApiError, errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import { authApi } from './auth-api'

export function LoginPage({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const mutation = useMutation({
    mutationFn: ({ login, password }: { login: string; password: string }) => authApi.login(login, password),
    onSuccess: () => {
      setPassword('')
      queryClient.clear()
      onAuthenticated()
      navigate('/', { replace: true })
    },
    onError: () => setPassword(''),
  })

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!login.trim() || !password || mutation.isPending) return
    mutation.mutate({ login: login.trim(), password })
  }

  const loginError = mutation.error instanceof ApiError && mutation.error.status === 401
    ? 'Неверный логин или пароль.'
    : errorText(mutation.error)

  return (
    <main className="login-layout">
      <div className="login-backdrop" aria-hidden="true" />
      <div className="login-content">
        <div className="login-intro">
          <div className="brand-mark brand-mark--large"><span>O</span><span className="brand-mark__dot" /></div>
          <p className="eyebrow">Единая рабочая среда OSS</p>
          <h1>Контроль заявок.<br /><span>Ясность в деталях.</span></h1>
          <p className="login-intro__description">
            Рабочее пространство call-центра и инженеров: от первого обращения до проверенного решения.
          </p>
          <div className="login-intro__security"><ShieldCheck size={18} /> Защищённая внутренняя система</div>
        </div>
        <div className="login-card">
          <div className="login-card__icon"><LockKeyhole size={23} /></div>
          <p className="eyebrow">OSS Control Center</p>
          <h2>Вход в систему</h2>
          <p className="muted">Используйте свою учётную запись OSS.</p>
          <form action="/api/v1/auth/login" method="post" onSubmit={submit} noValidate>
            <label className="field">
              <span>Логин</span>
              <input
                id="username"
                name="username"
                autoComplete="username"
                maxLength={256}
                value={login}
                onChange={(event) => setLogin(event.target.value)}
                required
                autoFocus
              />
            </label>
            <label className="field">
              <span>Пароль</span>
              <input
                id="current-password"
                name="password"
                type="password"
                autoComplete="current-password"
                maxLength={1024}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </label>
            {mutation.isError && <Notice tone="error">{loginError}</Notice>}
            <Button variant="primary" type="submit" disabled={mutation.isPending || !login.trim() || !password}>
              {mutation.isPending ? 'Проверяем доступ…' : 'Войти'}
              {!mutation.isPending && <ArrowRight size={17} aria-hidden="true" />}
            </Button>
          </form>
          <p className="login-card__foot">Доступ определяется назначенной ролью. Самостоятельная регистрация недоступна.</p>
        </div>
      </div>
      <div className="login-footer">OSS CONTROL CENTER <span>•</span> ВНУТРЕННИЙ КОНТУР</div>
    </main>
  )
}
