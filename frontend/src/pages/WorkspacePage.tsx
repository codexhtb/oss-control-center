import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, Database, KeyRound, Server, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router'
import type { MeResponse, ReadyResponse } from '../shared/api/types'
import { apiRequest, errorText } from '../shared/api/client'
import { Notice } from '../shared/ui/Notice'
import { Panel } from '../shared/ui/Panel'
import { roleLabel } from '../layout/AppShell'

export function WorkspacePage({ session }: { session: MeResponse }) {
  const health = useQuery({
    queryKey: ['health', 'ready'],
    queryFn: ({ signal }) => apiRequest<ReadyResponse>('/api/v1/health/ready', { signal }),
    refetchInterval: 30_000,
  })
  const hasDeskAccess = session.permissions.includes('ticket.read')

  return (
    <div className="page page--overview">
      <div className="page-heading">
        <div><p className="eyebrow">РАБОЧАЯ СРЕДА / ОБЗОР</p><h1>Добрый день, {session.user.display_name}</h1><p className="page-heading__description">Ваш доступ и состояние обязательных сервисов в одном месте.</p></div>
        <div className="role-chip"><ShieldCheck size={17} /> {roleLabel(session.user.role)}</div>
      </div>
      <div className="overview-grid">
        <Panel className="welcome-panel">
          <div className="panel-icon panel-icon--accent"><KeyRound size={22} /></div>
          <p className="eyebrow">ВАША СЕССИЯ</p>
          <h2>Сессия активна</h2>
          <p>Вы вошли как <strong>{session.user.login}</strong>. Доступные действия определяются правами вашей роли и проверяются сервером.</p>
          <Link className="text-link" to="/account">Посмотреть права доступа <ArrowUpRight size={16} /></Link>
        </Panel>
        <Panel className="health-panel">
          <div className="panel-heading"><div className="panel-icon"><Server size={20} /></div><span className="eyebrow">СОСТОЯНИЕ СИСТЕМЫ</span></div>
          <h2>Backend API</h2>
          {health.isPending ? (
            <p className="health-reading" role="status">Проверяем доступность…</p>
          ) : health.isError ? (
            <Notice tone="error">{errorText(health.error)}</Notice>
          ) : (
            <div className="health-reading"><span className="health-dot" /> <strong>Доступен</strong><span>Readiness подтверждена сервером</span></div>
          )}
          <div className="health-panel__divider" />
          <div className="health-row"><Database size={18} /><span>PostgreSQL</span><strong>{health.data?.dependencies.postgres === 'up' ? 'Доступен' : 'Нет подтверждения'}</strong></div>
          <p className="health-panel__note">Данные обновляются по ответу <code>/api/v1/health/ready</code>.</p>
        </Panel>
      </div>
      <div className="section-heading"><div><p className="eyebrow">РАБОЧИЕ ИНСТРУМЕНТЫ</p><h2>Следующий этап интерфейса</h2></div></div>
      {hasDeskAccess ? (
        <div className="upcoming-panel"><div className="upcoming-panel__line" /><div><strong>ServiceDesk</strong><p>Список заявок, создание и карточка появятся в Stage 3B. Здесь пока нет показателей или записей, которых backend не предоставил интерфейсу.</p></div><span className="stage-tag">STAGE 3B</span></div>
      ) : (
        <Notice>Техническая роль администратора не имеет доступа к заявкам и Subscriber 360. Управление пользователями будет оформлено отдельным интерфейсом.</Notice>
      )}
    </div>
  )
}
