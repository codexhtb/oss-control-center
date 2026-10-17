import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { apiRequest, errorText } from '../shared/api/client'
import type { Permission, Role, User } from '../shared/api/types'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { roleLabel } from '../layout/AppShell'
import { CategoryManager } from '../features/tickets/CategoryManager'
import { PasswordInput } from '../shared/ui/PasswordInput'

type ManagedUser = User & { permissions: Permission[] }
type Page<T> = { items: T[]; page: number; size: number; total: number }
type AuditRow = { id: string; actor_name: string | null; target_name: string | null; action: string; outcome: string; details: Record<string, unknown> | null; occurred_at: string }

const permissionGroups: { title: string; values: { key: Permission; label: string }[] }[] = [
  { title: 'Пользователи', values: [
    { key: 'users.read', label: 'Просмотр сотрудников' }, { key: 'users.manage', label: 'Управление сотрудниками' },
  ] },
  { title: 'Заявки', values: [
    { key: 'tickets.create', label: 'Создание' }, { key: 'tickets.read.all', label: 'Просмотр всех' },
    { key: 'tickets.read.own', label: 'Просмотр своих' }, { key: 'tickets.edit.all', label: 'Изменение всех' },
    { key: 'tickets.edit.own', label: 'Изменение своих' }, { key: 'tickets.comment', label: 'Комментарии' },
    { key: 'tickets.delete.own', label: 'Удаление своих' }, { key: 'tickets.delete.all', label: 'Удаление всех' },
    { key: 'tickets.restore', label: 'Восстановление из корзины' }, { key: 'tickets.purge', label: 'Окончательное удаление' },
    { key: 'tickets.assign', label: 'Назначение' }, { key: 'tickets.start', label: 'Начало работы' },
    { key: 'tickets.diagnose', label: 'Диагностика' }, { key: 'tickets.resolve', label: 'Решение' },
    { key: 'tickets.callback', label: 'Контрольный обзвон' },
    { key: 'tickets.close', label: 'Закрытие без обзвона' }, { key: 'tickets.field_work', label: 'Выездные работы' },
  ] },
  { title: 'Абоненты и мониторинг', values: [
    { key: 'subscribers.read', label: 'Карточки абонентов' },
    { key: 'subscribers.finance.read', label: 'Финансовые сведения' },
    { key: 'diagnostics.read', label: 'Результаты диагностики' },
    { key: 'diagnostics.run', label: 'Запуск диагностики' },
    { key: 'audit.read', label: 'Журнал действий' },
  ] },
]

const presets: Record<Role, Permission[]> = {
  operator: ['subscribers.read', 'subscribers.finance.read', 'tickets.read.all', 'tickets.create', 'tickets.edit.own', 'tickets.comment', 'diagnostics.read'],
  engineer: ['subscribers.read', 'tickets.read.all', 'tickets.create', 'tickets.edit.all', 'tickets.comment', 'tickets.start', 'tickets.assign', 'tickets.field_work', 'tickets.diagnose', 'diagnostics.read', 'diagnostics.run', 'tickets.resolve', 'tickets.close', 'audit.read'],
  admin: permissionGroups.flatMap((group) => group.values.map((entry) => entry.key)),
}

function generatePassword(): string {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-_'
  const bytes = crypto.getRandomValues(new Uint8Array(24))
  return Array.from(bytes, (value) => alphabet[value % alphabet.length]).join('')
}

function PermissionEditor({ selected, change }: { selected: Permission[]; change: (next: Permission[]) => void }) {
  return <div className="permission-editor">{permissionGroups.map((group) => <fieldset key={group.title}>
    <legend>{group.title}</legend>
    <div className="permission-editor__grid">{group.values.map(({ key, label }) => <label key={key} className="permission-editor__item">
      <input type="checkbox" checked={selected.includes(key)} onChange={(event) => change(event.target.checked ? [...selected, key] : selected.filter((item) => item !== key))} />
      <span>{label}</span>
    </label>)}</div>
  </fieldset>)}</div>
}

export function UsersPage() {
  const queryClient = useQueryClient()
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [login, setLogin] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState<Role>('operator')
  const [permissions, setPermissions] = useState<Permission[]>(presets.operator)
  const [temporaryPassword, setTemporaryPassword] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const [passwordRepeat, setPasswordRepeat] = useState('')
  const [mustChangePassword, setMustChangePassword] = useState(false)
  const [changingPassword, setChangingPassword] = useState(false)
  const [passwordWasGenerated, setPasswordWasGenerated] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [message, setMessage] = useState('')
  const [showAudit, setShowAudit] = useState(false)
  const users = useQuery({ queryKey: ['users', page], queryFn: () => apiRequest<Page<ManagedUser>>(`/api/v1/users?page=${page}&size=20`) })
  const audit = useQuery({ queryKey: ['users-audit'], queryFn: () => apiRequest<Page<AuditRow>>('/api/v1/users/audit?size=20'), enabled: showAudit })
  const selected = users.data?.items.find((user) => user.id === selectedId)

  async function save(action: () => Promise<unknown>, success: string) {
    if (busy) return
    setBusy(true); setError(null); setMessage('')
    try {
      await action()
      await Promise.all([queryClient.invalidateQueries({ queryKey: ['users'] }), queryClient.invalidateQueries({ queryKey: ['users-audit'] })])
      setMessage(success)
    } catch (reason) { setError(reason) }
    finally { setBusy(false) }
  }

  function beginCreate() {
    setSelectedId(null); setCreating(true); setLogin(''); setName(''); setRole('operator'); setPermissions(presets.operator)
    setTemporaryPassword(null); setPassword(''); setPasswordRepeat(''); setMustChangePassword(false)
    setChangingPassword(false); setPasswordWasGenerated(false); setError(null)
  }

  function beginEdit(user: ManagedUser) {
    setCreating(false); setSelectedId(user.id); setLogin(user.login); setName(user.display_name)
    setRole(user.role); setPermissions(user.permissions); setTemporaryPassword(null); setPassword('')
    setPasswordRepeat(''); setMustChangePassword(false); setChangingPassword(false)
    setPasswordWasGenerated(false); setError(null)
  }

  function fillGeneratedPassword() {
    const generated = generatePassword()
    setPassword(generated); setPasswordRepeat(generated); setPasswordWasGenerated(true)
  }

  function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!password || password !== passwordRepeat) return
    void save(async () => {
      const result = await apiRequest<ManagedUser & { temporary_password: string | null }>('/api/v1/users', {
        method: 'POST', json: { login, display_name: name, role, permissions, password, must_change_password: mustChangePassword },
      })
      if (passwordWasGenerated) setTemporaryPassword(password)
      setPassword(''); setPasswordRepeat(''); setPasswordWasGenerated(false)
      setCreating(false); setSelectedId(result.id)
    }, 'Пользователь создан.')
  }

  function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selected || !password || password !== passwordRepeat) return
    const nextPassword = password
    const generated = passwordWasGenerated
    void save(async () => {
      await apiRequest(`/api/v1/users/${selected.id}/password`, {
        method: 'PUT', json: { new_password: nextPassword, must_change_password: mustChangePassword },
      })
      if (generated) setTemporaryPassword(nextPassword)
      setPassword(''); setPasswordRepeat(''); setMustChangePassword(false)
      setPasswordWasGenerated(false); setChangingPassword(false)
    }, 'Пароль изменён, прежние сессии отозваны.')
  }

  return <div className="page desk-page">
    <div className="page-heading"><div><p className="eyebrow">ДОСТУП / СОТРУДНИКИ</p><h1>Пользователи</h1></div><div className="user-actions"><Link to="/trash" className="back-link">Корзина заявок</Link><Button variant="primary" onClick={beginCreate}>Создать пользователя</Button></div></div>
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
    {message && <Notice>{message}</Notice>}
    {temporaryPassword && <div className="detail-panel" role="status"><h2>Сгенерированный пароль</h2><p>Скопируйте и передайте его сотруднику защищённым способом.</p><code className="temporary-password">{temporaryPassword}</code><div><Button onClick={() => setTemporaryPassword(null)}>Скрыть пароль</Button></div></div>}
    <div className="ticket-detail-layout"><div className="ticket-detail-main">
      <section className="detail-panel"><h2>Сотрудники</h2>
        {users.isPending ? <p role="status">Загружаем список…</p> : users.isError ? <Notice tone="error">{errorText(users.error)}</Notice> : <>
          <div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Имя</th><th>Логин</th><th>Профиль</th><th>Статус</th><th>Разрешения</th></tr></thead><tbody>
            {users.data.items.map((user) => <tr key={user.id}><td><button type="button" className="text-link" onClick={() => beginEdit(user)}>{user.display_name}</button></td><td>{user.login}</td><td>{roleLabel(user.role)}</td><td>{user.is_active ? 'Активен' : 'Отключён'}</td><td>{user.permissions.length}</td></tr>)}
          </tbody></table></div>
          <div className="fact-pager"><Button variant="quiet" disabled={page <= 1} onClick={() => setPage(page - 1)}>Назад</Button><span>{page} / {Math.max(1, Math.ceil(users.data.total / users.data.size))}</span><Button variant="quiet" disabled={page * users.data.size >= users.data.total} onClick={() => setPage(page + 1)}>Далее</Button></div>
        </>}
      </section>
      <CategoryManager />
      <section className="detail-panel"><div className="detail-panel__heading"><h2>История административных действий</h2><Button variant="quiet" onClick={() => setShowAudit(!showAudit)}>{showAudit ? 'Скрыть' : 'Показать'}</Button></div>
        {showAudit && (audit.isPending ? <p role="status">Загружаем историю…</p> : audit.isError ? <Notice tone="error">{errorText(audit.error)}</Notice> : <div className="fact-list">{audit.data.items.map((row) => <div className="fact-item" key={row.id}><strong>{row.action}</strong><span>{new Date(row.occurred_at).toLocaleString('ru-RU')}</span><span>Исполнитель: {row.actor_name ?? 'Система'}</span><span>Объект: {row.target_name ?? '—'}</span><pre>{JSON.stringify(row.details, null, 2)}</pre></div>)}</div>)}
      </section>
    </div><aside className="ticket-detail-rail">{(creating || selected) && <section className="detail-panel detail-panel--rail">
      <h2>{creating ? 'Новый сотрудник' : 'Настройки сотрудника'}</h2>
      <form className="inline-form" onSubmit={creating ? submitCreate : (event) => { event.preventDefault(); if (selected) void save(() => apiRequest(`/api/v1/users/${selected.id}`, { method: 'PATCH', json: { login, display_name: name } }), 'Данные сохранены.') }}>
        <label className="field"><span>Логин</span><input value={login} onChange={(event) => setLogin(event.target.value)} required maxLength={64} /></label>
        <label className="field"><span>Имя сотрудника</span><input value={name} onChange={(event) => setName(event.target.value)} required maxLength={150} /></label>
        {creating && <label className="field"><span>Базовый профиль</span><select value={role} onChange={(event) => { const next = event.target.value as Role; setRole(next); setPermissions(presets[next]) }}><option value="operator">Оператор</option><option value="engineer">Инженер OSS</option><option value="admin">Администратор</option></select></label>}
        {creating && <div className="password-editor"><PasswordInput label="Пароль" name="new-password" value={password} onChange={(value) => { setPassword(value); setPasswordWasGenerated(false) }} /><PasswordInput label="Подтвердите пароль" name="repeat-password" value={passwordRepeat} onChange={setPasswordRepeat} />{password && passwordRepeat && password !== passwordRepeat && <Notice tone="error">Пароли не совпадают.</Notice>}<Button type="button" variant="quiet" onClick={fillGeneratedPassword}>Сгенерировать пароль</Button><label className="desk-check"><input type="checkbox" checked={mustChangePassword} onChange={(event) => setMustChangePassword(event.target.checked)} /> Потребовать смену при первом входе</label></div>}
        {creating && <PermissionEditor selected={permissions} change={setPermissions} />}
        <Button type="submit" disabled={busy || !login.trim() || !name.trim() || (creating && (!password || password !== passwordRepeat))}>{creating ? 'Создать' : 'Сохранить имя и логин'}</Button>
      </form>
      {selected && <>
        <PermissionEditor selected={permissions} change={setPermissions} />
        <Button disabled={busy || JSON.stringify([...permissions].sort()) === JSON.stringify([...selected.permissions].sort())} onClick={() => void save(() => apiRequest(`/api/v1/users/${selected.id}/permissions`, { method: 'PUT', json: { permissions } }), 'Разрешения обновлены.')}>Сохранить разрешения</Button>
        {!changingPassword && <Button variant="secondary" disabled={busy} onClick={() => { setChangingPassword(true); setPassword(''); setPasswordRepeat(''); setMustChangePassword(false); setPasswordWasGenerated(false) }}>Изменить пароль</Button>}
        {changingPassword && <form className="inline-form password-editor" onSubmit={submitPassword}><h3>Новый пароль</h3><PasswordInput label="Пароль" name="new-password" value={password} onChange={(value) => { setPassword(value); setPasswordWasGenerated(false) }} /><PasswordInput label="Подтвердите пароль" name="repeat-password" value={passwordRepeat} onChange={setPasswordRepeat} />{password && passwordRepeat && password !== passwordRepeat && <Notice tone="error">Пароли не совпадают.</Notice>}<Button type="button" variant="quiet" onClick={fillGeneratedPassword}>Сгенерировать пароль</Button><label className="desk-check"><input type="checkbox" checked={mustChangePassword} onChange={(event) => setMustChangePassword(event.target.checked)} /> Потребовать смену при первом входе</label><div className="user-actions"><Button type="submit" disabled={busy || !password || password !== passwordRepeat}>Сохранить пароль</Button><Button type="button" variant="quiet" disabled={busy} onClick={() => { setChangingPassword(false); setPassword(''); setPasswordRepeat('') }}>Отмена</Button></div></form>}
        <div className="user-actions">
          <Button variant="quiet" disabled={busy} onClick={() => void save(() => apiRequest(`/api/v1/users/${selected.id}/${selected.is_active ? 'deactivate' : 'reactivate'}`, { method: 'POST' }), selected.is_active ? 'Доступ отключён.' : 'Доступ восстановлен.')}>{selected.is_active ? 'Отключить' : 'Включить'}</Button></div>
      </>}
    </section>}</aside></div>
  </div>
}
