import { Fingerprint, ShieldCheck } from 'lucide-react'
import type { MeResponse, Permission } from '../shared/api/types'
import { Panel } from '../shared/ui/Panel'
import { roleLabel } from '../layout/AppShell'

const permissionLabels: Partial<Record<Permission, string>> = {
  'subscriber.read': 'Просмотр Subscriber 360',
  'ticket.read': 'Просмотр заявок',
  'ticket.create': 'Создание заявок',
  'ticket.comment': 'Комментарии',
  'ticket.start': 'Начало работы',
  'ticket.assign': 'Назначение инженера',
  'ticket.field_work': 'Запрос выездных работ',
  'ticket.manual_diagnostic': 'Ручная диагностика',
  'diagnostics.run': 'Проверка мониторинга',
  'ticket.decision': 'Решения',
  'ticket.close': 'Закрытие заявок',
  'ticket.audit.read': 'Чтение аудита заявки',
  'field_dispatch.send': 'Field Dispatch — функция следующего этапа',
  'identity.manage': 'Управление учётными записями',
}

export function AccountPage({ session }: { session: MeResponse }) {
  return (
    <div className="page page--account">
      <div className="page-heading"><div><p className="eyebrow">УЧЁТНАЯ ЗАПИСЬ / ДОСТУП</p><h1>Мой доступ</h1><p className="page-heading__description">Роль и права получены из текущей серверной сессии.</p></div></div>
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
    </div>
  )
}
