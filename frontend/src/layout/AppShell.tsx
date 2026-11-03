import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, LayoutGrid, LogOut, MessageCircle, PanelLeftClose, Search, Ticket, Trash2, Users } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router'
import type { MeResponse, Role } from '../shared/api/types'
import { errorText } from '../shared/api/client'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { authApi } from '../features/auth/auth-api'
import { NotificationBell } from '../features/notifications/NotificationBell'
import { useChatRealtime } from '../features/chat/ChatRealtime'

export function roleLabel(role: Role): string {
  return { operator: 'Оператор', engineer: 'Инженер OSS', admin: 'Администратор' }[role]
}

export function AppShell({ session, children, onLoggedOut }: { session: MeResponse; children: ReactNode; onLoggedOut: () => void }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const chat = useChatRealtime()
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
          {session.permissions.includes('subscribers.read') && <NavLink to="/subscribers" className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}><Search size={18} /> <span>Абоненты</span> <ArrowRight className="nav-link__arrow" size={15} /></NavLink>}
          <NavLink to="/chat" className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}><MessageCircle size={18} /> <span>Чат</span>{!!chat.data?.unread_count && <span className="chat-nav-count" aria-label={`Непрочитанных: ${chat.data.unread_count}`}>{chat.data.unread_count > 99 ? '99+' : chat.data.unread_count}</span>}</NavLink>
          {session.permissions.includes('users.manage') && <NavLink to="/users" className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}><Users size={18} /> <span>Пользователи</span> <ArrowRight className="nav-link__arrow" size={15} /></NavLink>}
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
          <div className="topbar__context"><span className="topbar__product">OSS CONTROL CENTER</span></div>
          <div className="topbar__right"><NotificationBell session={session} /><NavLink to="/account" className="topbar__user" aria-label={`Мой профиль: ${session.user.display_name}`}><span className="topbar__user-avatar" aria-hidden="true">{session.user.display_name.trim().charAt(0).toLocaleUpperCase('ru')}</span><span className="topbar__user-name">{session.user.display_name}</span></NavLink></div>
        </header>
        <main id="main-content" className="main-content">
          {logout.isError && <Notice tone="error">Не удалось завершить сессию. {errorText(logout.error)}</Notice>}
          {children}
        </main>
        <footer className="shell-footer shell-footer--actions">
          <Button variant="quiet" icon={<LogOut size={16} />} onClick={() => logout.mutate()} disabled={logout.isPending}>
            {logout.isPending ? 'Выход…' : 'Выйти'}
          </Button>
        </footer>
      </div>
    </div>
  )
}
