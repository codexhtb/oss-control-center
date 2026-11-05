import { useState } from 'react'
import { Link } from 'react-router'
import type { MeResponse } from '../shared/api/types'
import { readDefaultQueue, saveDefaultQueue, type TicketQueue } from '../features/tickets/queue-preference'
import { Notice } from '../shared/ui/Notice'

export function SettingsPage({ session }: { session: MeResponse }) {
  const canManage = session.permissions.includes('users.manage')
  const canReadAllTickets = session.permissions.includes('tickets.read.all')
  const [defaultQueue, setDefaultQueue] = useState(() => readDefaultQueue(session.user.id))
  const [saveFailed, setSaveFailed] = useState(false)

  function changeQueue(queue: TicketQueue) {
    if (saveDefaultQueue(session.user.id, queue)) {
      setDefaultQueue(queue)
      setSaveFailed(false)
    } else setSaveFailed(true)
  }

  return <div className="page settings-page">
    <div className="page-heading"><div><h1>Настройки</h1></div></div>
    {canManage && <section className="detail-panel settings-section" aria-label="Управление Service Desk">
      <h2>Управление Service Desk</h2>
      <div className="settings-links">
        {canManage && <Link to="/users?section=categories"><strong>Темы обращений</strong><span>Названия, порядок и доступность для новых заявок</span></Link>}
        {canManage && <Link to="/users?section=employees"><strong>Сотрудники и разрешения</strong><span>Учётные записи и персональный доступ</span></Link>}
        {canManage && <Link to="/users?section=audit"><strong>История действий</strong><span>Административные изменения</span></Link>}
      </div>
    </section>}
    {canReadAllTickets && <section className="detail-panel settings-section" aria-label="Личные настройки">
      <h2>Личные настройки</h2>
      <label className="field"><span>Очередь при открытии списка заявок</span>
        <select aria-label="Очередь по умолчанию" value={defaultQueue} onChange={(event) => changeQueue(event.target.value as TicketQueue)}>
          <option value="all">Все заявки</option><option value="waiting">Ожидают инженера</option>
          <option value="mine">Взяты мной</option><option value="callback">Решено · обзвон</option>
        </select>
      </label>
      <p className="muted">Сохраняется для вашей учётной записи в этом браузере. Прямая ссылка с фильтрами имеет приоритет.</p>
      {saveFailed && <Notice tone="error">Браузер не сохранил выбор очереди.</Notice>}
    </section>}
  </div>
}
