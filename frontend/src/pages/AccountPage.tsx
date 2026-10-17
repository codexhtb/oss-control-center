import { Fingerprint, ShieldCheck } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import type { MeResponse, Permission } from '../shared/api/types'
import { Panel } from '../shared/ui/Panel'
import { roleLabel } from '../layout/AppShell'
import { authApi } from '../features/auth/auth-api'
import { errorText } from '../shared/api/client'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { PasswordInput } from '../shared/ui/PasswordInput'

const permissionLabels: Partial<Record<Permission, string>> = {
  'subscribers.read': 'Просмотр Subscriber 360',
  'subscribers.finance.read': 'Финансовые сведения абонента',
  'tickets.read.all': 'Просмотр заявок',
  'tickets.read.own': 'Просмотр своих заявок',
  'tickets.edit.all': 'Редактирование всех заявок',
  'tickets.edit.own': 'Редактирование своих заявок',
  'tickets.create': 'Создание заявок',
  'tickets.comment': 'Комментарии',
  'tickets.start': 'Начало работы',
  'tickets.assign': 'Назначение инженера',
  'tickets.field_work': 'Запрос выездных работ',
  'tickets.diagnose': 'Ручная диагностика',
  'diagnostics.run': 'Проверка мониторинга',
  'diagnostics.read': 'Чтение диагностики',
  'tickets.resolve': 'Решения',
  'tickets.callback': 'Контрольный обзвон',
  'tickets.close': 'Закрытие заявок',
  'audit.read': 'Чтение аудита заявки',
  'users.manage': 'Управление учётными записями',
  'users.read': 'Просмотр сотрудников',
}

export function AccountPage({ session, onPasswordChanged }: { session: MeResponse; onPasswordChanged: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

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
      <div className="page-heading"><div><p className="eyebrow">УЧЁТНАЯ ЗАПИСЬ / ДОСТУП</p><h1>Мой доступ</h1><p className="page-heading__description">Разрешения получены с сервера и действуют для текущей учётной записи.</p></div></div>
      <div className="account-grid">
        <Panel className="identity-panel">
          <div className="panel-icon panel-icon--accent"><Fingerprint size={22} /></div>
          <p className="eyebrow">УЧЁТНАЯ ЗАПИСЬ</p>
          <h2>{session.user.display_name}</h2>
          <dl className="detail-list"><div><dt>Логин</dt><dd>{session.user.login}</dd></div><div><dt>Роль</dt><dd>{roleLabel(session.user.role)}</dd></div><div><dt>Статус</dt><dd>{session.user.is_active ? 'Активна' : 'Неактивна'}</dd></div></dl>
        </Panel>
        <Panel className="permissions-panel">
          <div className="permissions-panel__heading"><div className="panel-icon"><ShieldCheck size={20} /></div><div><p className="eyebrow">ПОЛНОМОЧИЯ</p><h2>Разрешённые действия</h2></div><span className="count-pill">{session.permissions.length}</span></div>
          <ul className="permission-list">
            {session.permissions.map((permission) => <li key={permission}><span className="permission-list__dot" /><span>{permissionLabels[permission] ?? permission}</span><code>{permission}</code></li>)}
          </ul>
          <p className="permissions-panel__note">Интерфейс показывает только доступные действия. Окончательное решение о доступе всегда принимает backend.</p>
        </Panel>
      </div>
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
  )
}
