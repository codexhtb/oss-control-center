import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, BarChart3, ClipboardList, LayoutGrid, LogOut, MessageCircle, PanelLeftClose, Search, Settings, Ticket, Trash2 } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router'
import type { MeResponse, Role } from '../shared/api/types'
import { errorText } from '../shared/api/client'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { authApi } from '../features/auth/auth-api'
import { NotificationBell } from '../features/notifications/NotificationBell'
import { useChatRealtime } from '../features/chat/ChatRealtime'
import { taskApi } from '../features/tasks/task-api'

export function roleLabel(role: Role): string {
  return { operator: 'Оператор', engineer: 'Инженер OSS', admin: 'Администратор' }[role]
}

export function AppShell({ session, children, onLoggedOut }: { session: MeResponse; children: ReactNode; onLoggedOut: () => void }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const chat = useChatRealtime()
  const canUseTasks = (session.permissions.includes('tasks.create') || session.permissions.includes('tasks.execute')) && (session.permissions.includes('tickets.read.all') || session.permissions.includes('tickets.read.own'))
  const taskNotifications = useQuery({ queryKey: ['internal-task-notifications', session.user.id], queryFn: ({ signal }) => taskApi.notifications(signal), enabled: canUseTasks, refetchInterval: 15_000 })
  const logout = useMutation({
    mutationFn: authApi.logout,
    onSuccess: () => {
      queryClient.clear()
      onLoggedOut()
      navigate('/login', { replace: true })
    },
  })

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">К основному содержимому</a>
      <button
        type="button"
        className="mobile-menu-button"
        onClick={() => setMobileOpen((open) => !open)}
        aria-expanded={mobileOpen}
        aria-label={mobileOpen ? 'Закрыть меню' : 'Открыть меню'}
      >
        <PanelLeftClose size={20} />
      </button>
      {mobileOpen && <button className="mobile-overlay" type="button" aria-label="Закрыть меню" onClick={() => setMobileOpen(false)} />}
      <aside className={`sidebar ${mobileOpen ? 'sidebar--open' : ''}`}>
        <div className="sidebar__brand">
          <div className="brand-mark"><span>O</span><span className="brand-mark__dot" /></div>
          <div><strong>OSS Control</strong><span>Center</span></div>
        </div>
        <nav className="sidebar__nav" aria-label="Основная навигация">
          <NavLink to="/" end className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}>
            <LayoutGrid size={18} /> <span>Обзор</span> <ArrowRight className="nav-link__arrow" size={15} />
          </NavLink>
          {(session.permissions.includes('tickets.read.all') || session.permissions.includes('tickets.read.own')) && <NavLink to="/tickets" className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}><Ticket size={18} /> <span>Заявки</span> <ArrowRight className="nav-link__arrow" size={15} /></NavLink>}
          {canUseTasks && <NavLink to="/tasks" className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}><ClipboardList size={18} /> <span>Задачи</span>{!!taskNotifications.data?.unread_count && <span className="chat-nav-count" aria-label={`Новых поручений: ${taskNotifications.data.unread_count}`}>{taskNotifications.data.unread_count > 99 ? '99+' : taskNotifications.data.unread_count}</span>}</NavLink>}
          {session.permissions.includes('reports.read') && (session.permissions.includes('tickets.read.all') || session.permissions.includes('tickets.read.own')) && <NavLink to="/reports" className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}><BarChart3 size={18} /> <span>Отчёты</span> <ArrowRight className="nav-link__arrow" size={15} /></NavLink>}
          {session.permissions.includes('subscribers.read') && <NavLink to="/subscribers" className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}><Search size={18} /> <span>Абоненты</span> <ArrowRight className="nav-link__arrow" size={15} /></NavLink>}
          <NavLink to="/chat" className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}><MessageCircle size={18} /> <span>Чат</span>{!!chat.data?.unread_count && <span className="chat-nav-count" aria-label={`Непрочитанных: ${chat.data.unread_count}`}>{chat.data.unread_count > 99 ? '99+' : chat.data.unread_count}</span>}</NavLink>
          {(session.permissions.includes('users.manage') || session.permissions.includes('settings.manage') || session.permissions.includes('tickets.read.all')) && <NavLink to="/settings" className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}><Settings size={18} /> <span>Настройки</span> <ArrowRight className="nav-link__arrow" size={15} /></NavLink>}
          {session.permissions.includes('tickets.read.all') && (session.permissions.includes('tickets.restore') || session.permissions.includes('tickets.purge')) && <NavLink to="/trash" className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}><Trash2 size={18} /> <span>Корзина</span> <ArrowRight className="nav-link__arrow" size={15} /></NavLink>}
        </nav>
        <div className="sidebar__spacer" />
        <div className="sidebar__profile">
          <div className="avatar">{session.user.display_name.trim().charAt(0).toLocaleUpperCase('ru')}</div>
          <div className="sidebar__profile-text"><strong>{session.user.display_name}</strong><span>{roleLabel(session.user.role)}</span></div>
        </div>
      </aside>
      <div className="shell-main">
        <header className="topbar">
          <div className="topbar__right">
            <NotificationBell session={session} />
            <NavLink to="/account" className="topbar__user" aria-label={`Мой профиль: ${session.user.display_name}`}><span className="topbar__user-avatar" aria-hidden="true">{session.user.display_name.trim().charAt(0).toLocaleUpperCase('ru')}</span><span className="topbar__user-name">{session.user.display_name}</span></NavLink>
            <Button className="topbar__logout" variant="quiet" icon={<LogOut size={16} />} aria-label="Выйти" onClick={() => logout.mutate()} disabled={logout.isPending}>
              <span className="topbar__logout-label">{logout.isPending ? 'Выход…' : 'Выйти'}</span>
            </Button>
          </div>
        </header>
        <main id="main-content" className="main-content">
          {logout.isError && <Notice tone="error">Не удалось завершить сессию. {errorText(logout.error)}</Notice>}
          {children}
        </main>
      </div>
    </div>
  )
}
