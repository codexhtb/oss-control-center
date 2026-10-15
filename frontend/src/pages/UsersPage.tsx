import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { apiRequest, errorText } from '../shared/api/client'
import type { Permission, Role, User } from '../shared/api/types'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { roleLabel } from '../layout/AppShell'

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
    { key: 'tickets.assign', label: 'Назначение' }, { key: 'tickets.start', label: 'Начало работы' },
    { key: 'tickets.diagnose', label: 'Диагностика' }, { key: 'tickets.resolve', label: 'Решение' },
    { key: 'tickets.close', label: 'Закрытие' }, { key: 'tickets.field_work', label: 'Выездные работы' },
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
    setTemporaryPassword(null); setError(null)
  }

  function beginEdit(user: ManagedUser) {
    setCreating(false); setSelectedId(user.id); setLogin(user.login); setName(user.display_name)
    setRole(user.role); setPermissions(user.permissions); setTemporaryPassword(null); setError(null)
  }

  function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void save(async () => {
      const result = await apiRequest<ManagedUser & { temporary_password: string }>('/api/v1/users', {
        method: 'POST', json: { login, display_name: name, role, permissions },
      })
      setTemporaryPassword(result.temporary_password)
      setCreating(false); setSelectedId(result.id)
    }, 'Пользователь создан. Передайте временный пароль защищённым способом.')
  }

  return <div className="page desk-page">
    <div className="page-heading"><div><p className="eyebrow">ДОСТУП / СОТРУДНИКИ</p><h1>Пользователи</h1></div><Button variant="primary" onClick={beginCreate}>Создать пользователя</Button></div>
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
    {message && <Notice>{message}</Notice>}
    {temporaryPassword && <div className="detail-panel" role="status"><h2>Временный пароль</h2><p>Показан один раз. После закрытия получить его снова нельзя; потребуется сброс.</p><code className="temporary-password">{temporaryPassword}</code><div><Button onClick={() => setTemporaryPassword(null)}>Скрыть пароль</Button></div></div>}
    <div className="ticket-detail-layout"><div className="ticket-detail-main">
      <section className="detail-panel"><h2>Сотрудники</h2>
        {users.isPending ? <p role="status">Загружаем список…</p> : users.isError ? <Notice tone="error">{errorText(users.error)}</Notice> : <>
          <div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Имя</th><th>Логин</th><th>Профиль</th><th>Статус</th><th>Разрешения</th></tr></thead><tbody>
            {users.data.items.map((user) => <tr key={user.id}><td><button type="button" className="text-link" onClick={() => beginEdit(user)}>{user.display_name}</button></td><td>{user.login}</td><td>{roleLabel(user.role)}</td><td>{user.is_active ? 'Активен' : 'Отключён'}</td><td>{user.permissions.length}</td></tr>)}
          </tbody></table></div>
          <div className="fact-pager"><Button variant="quiet" disabled={page <= 1} onClick={() => setPage(page - 1)}>Назад</Button><span>{page} / {Math.max(1, Math.ceil(users.data.total / users.data.size))}</span><Button variant="quiet" disabled={page * users.data.size >= users.data.total} onClick={() => setPage(page + 1)}>Далее</Button></div>
        </>}
      </section>
      <section className="detail-panel"><div className="detail-panel__heading"><h2>История административных действий</h2><Button variant="quiet" onClick={() => setShowAudit(!showAudit)}>{showAudit ? 'Скрыть' : 'Показать'}</Button></div>
        {showAudit && (audit.isPending ? <p role="status">Загружаем историю…</p> : audit.isError ? <Notice tone="error">{errorText(audit.error)}</Notice> : <div className="fact-list">{audit.data.items.map((row) => <div className="fact-item" key={row.id}><strong>{row.action}</strong><span>{new Date(row.occurred_at).toLocaleString('ru-RU')}</span><span>Исполнитель: {row.actor_name ?? 'Система'}</span><span>Объект: {row.target_name ?? '—'}</span><pre>{JSON.stringify(row.details, null, 2)}</pre></div>)}</div>)}
      </section>
    </div><aside className="ticket-detail-rail">{(creating || selected) && <section className="detail-panel detail-panel--rail">
      <h2>{creating ? 'Новый сотрудник' : 'Настройки сотрудника'}</h2>
      <form className="inline-form" onSubmit={creating ? submitCreate : (event) => { event.preventDefault(); if (selected) void save(() => apiRequest(`/api/v1/users/${selected.id}`, { method: 'PATCH', json: { login, display_name: name } }), 'Данные сохранены.') }}>
        <label className="field"><span>Логин</span><input value={login} onChange={(event) => setLogin(event.target.value)} required maxLength={64} /></label>
        <label className="field"><span>Имя сотрудника</span><input value={name} onChange={(event) => setName(event.target.value)} required maxLength={150} /></label>
        {creating && <label className="field"><span>Базовый профиль</span><select value={role} onChange={(event) => { const next = event.target.value as Role; setRole(next); setPermissions(presets[next]) }}><option value="operator">Оператор</option><option value="engineer">Инженер OSS</option><option value="admin">Администратор</option></select></label>}
        {creating && <PermissionEditor selected={permissions} change={setPermissions} />}
        <Button type="submit" disabled={busy || !login.trim() || !name.trim()}>{creating ? 'Создать' : 'Сохранить имя и логин'}</Button>
      </form>
      {selected && <>
        <PermissionEditor selected={permissions} change={setPermissions} />
        <Button disabled={busy || JSON.stringify([...permissions].sort()) === JSON.stringify([...selected.permissions].sort())} onClick={() => void save(() => apiRequest(`/api/v1/users/${selected.id}/permissions`, { method: 'PUT', json: { permissions } }), 'Разрешения обновлены.')}>Сохранить разрешения</Button>
        <div className="user-actions"><Button variant="secondary" disabled={busy} onClick={() => void save(async () => { const result = await apiRequest<{ temporary_password: string }>(`/api/v1/users/${selected.id}/reset-password`, { method: 'POST' }); setTemporaryPassword(result.temporary_password) }, 'Пароль сброшен, прежние сессии отозваны.')}>Выдать временный пароль</Button>
          <Button variant="quiet" disabled={busy} onClick={() => void save(() => apiRequest(`/api/v1/users/${selected.id}/${selected.is_active ? 'deactivate' : 'reactivate'}`, { method: 'POST' }), selected.is_active ? 'Доступ отключён.' : 'Доступ восстановлен.')}>{selected.is_active ? 'Отключить' : 'Включить'}</Button></div>
      </>}
    </section>}</aside></div>
  </div>
}
