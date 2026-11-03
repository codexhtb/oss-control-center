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
  { title: 'Чат', values: [{ key: 'chat.messages.delete', label: 'Удаление сообщений (администратор)' }] },
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

function initials(name: string): string {
  return name.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toLocaleUpperCase('ru') || '—'
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

export function UsersPage({ currentUser }: { currentUser: User }) {
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
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [passwordWasGenerated, setPasswordWasGenerated] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [message, setMessage] = useState('')
  const [section, setSection] = useState<'employees' | 'categories' | 'audit'>('employees')
  const [editorTab, setEditorTab] = useState<'main' | 'permissions' | 'security'>('main')
  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState<Role | 'all'>('all')
  const [activityFilter, setActivityFilter] = useState<'all' | 'active' | 'inactive'>('all')
  const [auditSearch, setAuditSearch] = useState('')
  const [auditOutcome, setAuditOutcome] = useState('all')
  const [auditPage, setAuditPage] = useState(1)
  const users = useQuery({ queryKey: ['users'], queryFn: async () => {
    const first = await apiRequest<Page<ManagedUser>>('/api/v1/users?page=1&size=100')
    const items = [...first.items]
    for (let nextPage = 2; items.length < first.total; nextPage++) {
      const next = await apiRequest<Page<ManagedUser>>(`/api/v1/users?page=${nextPage}&size=100`)
      if (!next.items.length) break
      items.push(...next.items)
    }
    return items
  } })
  const audit = useQuery({ queryKey: ['users-audit', auditPage], queryFn: () => apiRequest<Page<AuditRow>>(`/api/v1/users/audit?page=${auditPage}&size=20`), enabled: section === 'audit' })
  const selected = users.data?.find((user) => user.id === selectedId)
  const visibleUsers = users.data?.filter((user) =>
    `${user.display_name} ${user.login}`.toLocaleLowerCase('ru').includes(search.trim().toLocaleLowerCase('ru')) &&
    (roleFilter === 'all' || user.role === roleFilter) &&
    (activityFilter === 'all' || user.is_active === (activityFilter === 'active')),
    ) ?? []
  const shownUsers = visibleUsers.slice((page - 1) * 20, page * 20)
  const visibleAudit = audit.data?.items.filter((row) =>
    `${row.actor_name ?? ''} ${row.target_name ?? ''} ${row.action}`.toLocaleLowerCase('ru').includes(auditSearch.trim().toLocaleLowerCase('ru')) &&
    (auditOutcome === 'all' || row.outcome === auditOutcome),
  ) ?? []

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
    setSection('employees'); setEditorTab('main')
    setSelectedId(null); setCreating(true); setLogin(''); setName(''); setRole('operator'); setPermissions(presets.operator)
    setTemporaryPassword(null); setPassword(''); setPasswordRepeat(''); setMustChangePassword(false)
    setChangingPassword(false); setPasswordWasGenerated(false); setConfirmDelete(false); setError(null)
  }

  function beginEdit(user: ManagedUser) {
    setSection('employees'); setEditorTab('main')
    setCreating(false); setSelectedId(user.id); setLogin(user.login); setName(user.display_name)
    setRole(user.role); setPermissions(user.permissions); setTemporaryPassword(null); setPassword('')
    setPasswordRepeat(''); setMustChangePassword(false); setChangingPassword(false)
    setPasswordWasGenerated(false); setConfirmDelete(false); setError(null)
  }

  function fillGeneratedPassword() {
    const generated = generatePassword()
    setPassword(generated); setPasswordRepeat(generated); setPasswordWasGenerated(true)
  }

  function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!login.trim() || !name.trim() || !password || password !== passwordRepeat) return
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

  return <div className="page desk-page users-page">
    <div className="page-heading"><div><p className="eyebrow">УПРАВЛЕНИЕ ДОСТУПОМ</p><h1>Пользователи</h1></div><div className="user-actions"><Link to="/trash" className="back-link">Корзина заявок</Link>{section === 'employees' && !creating && !selected && <Button variant="primary" onClick={beginCreate}>Создать пользователя</Button>}</div></div>
    {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
    {message && <Notice>{message}</Notice>}
    {temporaryPassword && <div className="detail-panel" role="status"><h2>Сгенерированный пароль</h2><p>Скопируйте и передайте его сотруднику защищённым способом.</p><code className="temporary-password">{temporaryPassword}</code><div><Button onClick={() => setTemporaryPassword(null)}>Скрыть пароль</Button></div></div>}
    <nav className="ticket-tabs" aria-label="Разделы пользователей">
      {([['employees', 'Сотрудники'], ['categories', 'Темы обращений'], ['audit', 'История действий']] as const).map(([key, label]) => <button key={key} type="button" className="ticket-tabs__tab" aria-current={section === key ? 'page' : undefined} onClick={() => { setSection(key); setCreating(false); setSelectedId(null); setError(null) }}>{label}</button>)}
    </nav>
    {section === 'employees' && (creating || selected ? <section className="detail-panel employee-editor">
      <div className="employee-editor__heading"><div><p className="eyebrow">СОТРУДНИКИ</p><h2>{creating ? 'Новый сотрудник' : selected?.display_name}</h2></div><Button variant="quiet" onClick={() => { setCreating(false); setSelectedId(null); setError(null) }}>К списку сотрудников</Button></div>
      <nav className="ticket-tabs" aria-label="Настройки сотрудника">
        {([['main', 'Основное'], ['permissions', 'Разрешения'], ['security', 'Безопасность']] as const).map(([key, label]) => <button key={key} type="button" className="ticket-tabs__tab" aria-current={editorTab === key ? 'page' : undefined} onClick={() => setEditorTab(key)}>{label}</button>)}
      </nav>
      {editorTab === 'main' && <form className="employee-editor__form" onSubmit={creating ? submitCreate : (event) => { event.preventDefault(); if (selected) void save(() => apiRequest(`/api/v1/users/${selected.id}`, { method: 'PATCH', json: { login, display_name: name } }), 'Данные сохранены.') }}>
        <div className="employee-editor__fields"><label className="field"><span>Логин</span><input value={login} onChange={(event) => setLogin(event.target.value)} required maxLength={64} /></label><label className="field"><span>Имя сотрудника</span><input value={name} onChange={(event) => setName(event.target.value)} required maxLength={150} /></label>
        {creating && <label className="field"><span>Базовый профиль</span><select value={role} onChange={(event) => { const next = event.target.value as Role; setRole(next); setPermissions(presets[next]) }}><option value="operator">Оператор</option><option value="engineer">Инженер OSS</option><option value="admin">Администратор</option></select></label>}</div>
        {creating && <p className="muted">Разрешения и пароль задаются в соседних вкладках до создания учётной записи.</p>}
        <Button type="submit" disabled={busy || !login.trim() || !name.trim() || (creating && (!password || password !== passwordRepeat))}>{creating ? 'Создать' : 'Сохранить имя и логин'}</Button>
      </form>}
      {editorTab === 'permissions' && <div className="employee-editor__form"><PermissionEditor selected={permissions} change={setPermissions} />
        {selected && <Button disabled={busy || JSON.stringify([...permissions].sort()) === JSON.stringify([...selected.permissions].sort())} onClick={() => void save(() => apiRequest(`/api/v1/users/${selected.id}/permissions`, { method: 'PUT', json: { permissions } }), 'Разрешения обновлены.')}>Сохранить разрешения</Button>}
        {creating && <p className="muted">Выбранные разрешения сохранятся при создании сотрудника.</p>}
      </div>}
      {editorTab === 'security' && <div className="employee-editor__form">
        {creating && <form className="password-editor employee-editor__password" onSubmit={submitCreate}><PasswordInput label="Пароль" name="new-password" value={password} onChange={(value) => { setPassword(value); setPasswordWasGenerated(false) }} /><PasswordInput label="Подтвердите пароль" name="repeat-password" value={passwordRepeat} onChange={setPasswordRepeat} />{password && passwordRepeat && password !== passwordRepeat && <Notice tone="error">Пароли не совпадают.</Notice>}<Button type="button" variant="quiet" onClick={fillGeneratedPassword}>Сгенерировать пароль</Button><label className="desk-check"><input type="checkbox" checked={mustChangePassword} onChange={(event) => setMustChangePassword(event.target.checked)} /> Потребовать смену при первом входе</label><Button type="submit" disabled={busy || !login.trim() || !name.trim() || !password || password !== passwordRepeat}>Создать</Button></form>}
        {selected && <>
          {!changingPassword && <Button variant="secondary" disabled={busy} onClick={() => { setChangingPassword(true); setPassword(''); setPasswordRepeat(''); setMustChangePassword(false); setPasswordWasGenerated(false) }}>Изменить пароль</Button>}
          {changingPassword && <form className="inline-form password-editor employee-editor__password" onSubmit={submitPassword}><h3>Новый пароль</h3><PasswordInput label="Пароль" name="new-password" value={password} onChange={(value) => { setPassword(value); setPasswordWasGenerated(false) }} /><PasswordInput label="Подтвердите пароль" name="repeat-password" value={passwordRepeat} onChange={setPasswordRepeat} />{password && passwordRepeat && password !== passwordRepeat && <Notice tone="error">Пароли не совпадают.</Notice>}<Button type="button" variant="quiet" onClick={fillGeneratedPassword}>Сгенерировать пароль</Button><label className="desk-check"><input type="checkbox" checked={mustChangePassword} onChange={(event) => setMustChangePassword(event.target.checked)} /> Потребовать смену при первом входе</label><div className="user-actions"><Button type="submit" disabled={busy || !password || password !== passwordRepeat}>Сохранить пароль</Button><Button type="button" variant="quiet" disabled={busy} onClick={() => { setChangingPassword(false); setPassword(''); setPasswordRepeat('') }}>Отмена</Button></div></form>}
          <div className="employee-editor__access"><div><strong>Доступ к системе</strong><span>{selected.is_active ? 'Учётная запись активна' : 'Учётная запись отключена'}</span></div><Button variant="quiet" disabled={busy} onClick={() => void save(() => apiRequest(`/api/v1/users/${selected.id}/${selected.is_active ? 'deactivate' : 'reactivate'}`, { method: 'POST' }), selected.is_active ? 'Доступ отключён.' : 'Доступ восстановлен.')}>{selected.is_active ? 'Отключить' : 'Включить'}</Button></div>
          {currentUser.role === 'admin' && currentUser.id !== selected.id && <div className="employee-editor__danger">
            <div><strong>Удалить сотрудника</strong><span>Доступ будет отозван, сотрудник исчезнет из списка. Заявки и история сохранятся.</span></div>
            {confirmDelete ? <div className="user-actions"><Button type="button" variant="quiet" disabled={busy} onClick={() => setConfirmDelete(false)}>Отмена</Button><Button type="button" className="button--danger" disabled={busy} onClick={() => void save(async () => { await apiRequest(`/api/v1/users/${selected.id}`, { method: 'DELETE' }); setSelectedId(null); setConfirmDelete(false) }, 'Сотрудник удалён. История сохранена.')}>Подтвердить удаление</Button></div> : <Button type="button" variant="quiet" disabled={busy} onClick={() => setConfirmDelete(true)}>Удалить сотрудника</Button>}
          </div>}
        </>}
      </div>}
    </section> : <section className="detail-panel"><div className="detail-panel__heading"><h2>Сотрудники</h2><span className="muted">Всего: {users.data?.length ?? '—'}</span></div>
      <div className="users-filters"><label className="field"><span>Поиск</span><input value={search} onChange={(event) => { setSearch(event.target.value); setPage(1) }} placeholder="Имя или логин" /></label><label className="field"><span>Роль</span><select value={roleFilter} onChange={(event) => { setRoleFilter(event.target.value as Role | 'all'); setPage(1) }}><option value="all">Все роли</option><option value="operator">Оператор</option><option value="engineer">Инженер OSS</option><option value="admin">Администратор</option></select></label><label className="field"><span>Активность</span><select value={activityFilter} onChange={(event) => { setActivityFilter(event.target.value as 'all' | 'active' | 'inactive'); setPage(1) }}><option value="all">Все</option><option value="active">Активные</option><option value="inactive">Отключённые</option></select></label></div>
      {users.isPending ? <p role="status">Загружаем список…</p> : users.isError ? <Notice tone="error">{errorText(users.error)}</Notice> : <>
        <div className="table-scroll"><table className="subscriber-table users-table"><thead><tr><th>Имя</th><th>Логин</th><th>Профиль</th><th>Статус</th><th>Разрешения</th></tr></thead><tbody>{shownUsers.map((user) => <tr key={user.id}><td><button type="button" className="employee-name" onClick={() => beginEdit(user)}><span className="employee-name__avatar" aria-hidden="true">{initials(user.display_name)}</span><span className="employee-name__label">{user.display_name}</span></button></td><td>{user.login}</td><td>{roleLabel(user.role)}</td><td>{user.is_active ? 'Активен' : 'Отключён'}</td><td>{user.permissions.length}</td></tr>)}</tbody></table></div>
        {visibleUsers.length === 0 && <p className="muted">Сотрудники не найдены.</p>}
        <div className="fact-pager"><Button variant="quiet" disabled={page <= 1} onClick={() => setPage(page - 1)}>Назад</Button><span>{page} / {Math.max(1, Math.ceil(visibleUsers.length / 20))}</span><Button variant="quiet" disabled={page * 20 >= visibleUsers.length} onClick={() => setPage(page + 1)}>Далее</Button></div>
      </>}
    </section>)}
    {section === 'categories' && <CategoryManager />}
    {section === 'audit' && <section className="detail-panel"><h2>История административных действий</h2><div className="users-filters"><label className="field"><span>Поиск на странице</span><input value={auditSearch} onChange={(event) => setAuditSearch(event.target.value)} placeholder="Действие, исполнитель, объект" /></label><label className="field"><span>Результат на странице</span><select value={auditOutcome} onChange={(event) => setAuditOutcome(event.target.value)}><option value="all">Все</option><option value="success">Успешно</option><option value="denied">Отказ</option><option value="rate_limited">Лимит входа</option></select></label></div>
      {audit.isPending ? <p role="status">Загружаем историю…</p> : audit.isError ? <Notice tone="error">{errorText(audit.error)}</Notice> : <>{visibleAudit.length ? <div className="fact-list">{visibleAudit.map((row) => <div className="fact-item" key={row.id}><strong>{row.action}</strong><span>{new Date(row.occurred_at).toLocaleString('ru-RU')}</span><span>Результат: {row.outcome}</span><span>Исполнитель: {row.actor_name ?? 'Система'}</span><span>Объект: {row.target_name ?? '—'}</span><pre>{JSON.stringify(row.details, null, 2)}</pre></div>)}</div> : <p className="muted">Записи не найдены на этой странице.</p>}<div className="fact-pager"><Button variant="quiet" disabled={auditPage <= 1} onClick={() => setAuditPage(auditPage - 1)}>Назад</Button><span>{auditPage} / {Math.max(1, Math.ceil(audit.data.total / audit.data.size))}</span><Button variant="quiet" disabled={auditPage * audit.data.size >= audit.data.total} onClick={() => setAuditPage(auditPage + 1)}>Далее</Button></div></>}
    </section>}
  </div>
}
