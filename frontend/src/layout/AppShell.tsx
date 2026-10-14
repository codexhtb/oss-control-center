import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Activity, ArrowRight, LayoutGrid, LogOut, PanelLeftClose, Shield, UserRound } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router'
import type { MeResponse, Role } from '../shared/api/types'
import { errorText } from '../shared/api/client'
import { Button } from '../shared/ui/Button'
import { Notice } from '../shared/ui/Notice'
import { authApi } from '../features/auth/auth-api'

export function roleLabel(role: Role): string {
  return { operator: 'Оператор', engineer: 'Инженер OSS', admin: 'Администратор' }[role]
}

export function AppShell({ session, children, onLoggedOut }: { session: MeResponse; children: ReactNode; onLoggedOut: () => void }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
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
        <div className="sidebar__section-label">РАБОЧАЯ ОБЛАСТЬ</div>
        <nav className="sidebar__nav" aria-label="Основная навигация">
          <NavLink to="/" end className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}>
            <LayoutGrid size={18} /> <span>Обзор</span> <ArrowRight className="nav-link__arrow" size={15} />
          </NavLink>
          <NavLink to="/account" className={({ isActive }) => `nav-link ${isActive ? 'nav-link--active' : ''}`} onClick={() => setMobileOpen(false)}>
            <UserRound size={18} /> <span>Мой доступ</span> <ArrowRight className="nav-link__arrow" size={15} />
          </NavLink>
        </nav>
        <div className="sidebar__spacer" />
        <div className="sidebar__environment"><Activity size={15} /><span>Внутренний контур OSS</span></div>
        <div className="sidebar__profile">
          <div className="avatar">{session.user.display_name.trim().charAt(0).toLocaleUpperCase('ru')}</div>
          <div className="sidebar__profile-text"><strong>{session.user.display_name}</strong><span>{roleLabel(session.user.role)}</span></div>
        </div>
      </aside>
      <div className="shell-main">
        <header className="topbar">
          <div className="topbar__context"><span className="topbar__product">OSS CONTROL CENTER</span><span className="topbar__divider">/</span><span>Рабочее пространство</span></div>
          <div className="topbar__right"><span className="topbar__secure"><Shield size={14} /> Защищённая сессия</span><span className="topbar__user">{session.user.login}</span></div>
        </header>
        <main id="main-content" className="main-content">
          {logout.isError && <Notice tone="error">Не удалось завершить сессию. {errorText(logout.error)}</Notice>}
          {children}
        </main>
        <footer className="shell-footer">
          <span>OSS Control Center · внутренняя система</span>
          <Button variant="quiet" icon={<LogOut size={16} />} onClick={() => logout.mutate()} disabled={logout.isPending}>
            {logout.isPending ? 'Выход…' : 'Выйти'}
          </Button>
        </footer>
      </div>
    </div>
  )
}
