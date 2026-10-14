import { useQuery } from '@tanstack/react-query'
import { errorText } from '../../shared/api/client'
import { Notice } from '../../shared/ui/Notice'
import { ticketApi } from './ticket-api'
import type { OnuResult } from './types'

const stateLabels = {
  ONLINE: 'Online', OFFLINE: 'Offline', DEREGISTERED: 'Deregistered', UNKNOWN: 'UNKNOWN',
}

function Candidate({ item }: { item: OnuResult['candidates'][number] }) {
  return <div className="onu-candidate">
    <div className="onu-candidate__heading"><strong>{item.olt || 'OLT неизвестен'} · {item.interface}</strong><span>На момент опроса: {stateLabels[item.state]}</span></div>
    <div className="subscriber-facts subscriber-facts--three">
      <div className="subscriber-fact"><span>MAC ONU</span><strong>{item.mac || 'Отсутствует'}</strong></div>
      <div className="subscriber-fact"><span>Serial</span><strong>{item.serial || 'Отсутствует'}</strong></div>
      <div className="subscriber-fact"><span>Причина отключения</span><strong>{item.reason || 'Не указана'}</strong></div>
      <div className="subscriber-fact"><span>ONU Rx</span><strong>{item.onu_rx_dbm === null ? 'Нет измерения' : `${item.onu_rx_dbm} dBm`}</strong></div>
      <div className="subscriber-fact"><span>OLT Rx</span><strong>{item.olt_rx_dbm === null ? 'Нет измерения' : `${item.olt_rx_dbm} dBm`}</strong></div>
      <div className="subscriber-fact"><span>ONU Tx</span><strong>{item.onu_tx_dbm === null ? 'Нет измерения' : `${item.onu_tx_dbm} dBm`}</strong></div>
    </div>
    <p className="muted">Последний опрос ONU: {item.inventory_polled_at || 'неизвестно'} · Оптика: {item.optical_measured_at || 'неизвестно'} (время источника, часовой пояс не подтверждён)</p>
  </div>
}

export function OnuCard({ result }: { result: OnuResult }) {
  if (result.availability === 'UNAVAILABLE') return <Notice tone="error">Источник BDCOM ONU не подключён или недоступен.</Notice>
  if (!result.candidates.length) return <p className="muted">ONU не сопоставлена с абонентом. {result.availability === 'CONFLICT' ? 'В NetUP есть противоречивые привязки.' : 'Подтверждённых данных нет.'}</p>
  return <div className="onu-candidates">
    <p className="muted">Кандидаты по OLT и VLAN. Принадлежность абоненту не подтверждена инженером.</p>
    {result.candidates.map((item) => <Candidate key={`${item.pon_type}-${item.interface}-${item.serial || item.mac}`} item={item} />)}
    {result.limitations.includes('onu_candidates_truncated') && <p className="muted">Показаны только первые 20 кандидатов.</p>}
    {result.limitations.includes('optical_values_may_have_different_ages') && <p className="muted">Время отдельного оптического значения может отличаться от времени общего обновления.</p>}
  </div>
}

export function SubscriberOnuCard({ lookupId, connectionId }: { lookupId: string; connectionId: string }) {
  const query = useQuery({ queryKey: ['subscriber-onu', lookupId, connectionId], queryFn: ({ signal }) => ticketApi.subscriberOnu(lookupId, connectionId, signal), retry: false, staleTime: 30_000 })
  return <div className="onu-section"><h4>BDCOM ONU</h4>{query.isPending ? <p className="muted">Проверяем источник ONU…</p> : query.isError ? <Notice tone="error">{errorText(query.error)}</Notice> : <OnuCard result={query.data} />}</div>
}

export function TicketOnuCard({ ticketId }: { ticketId: string }) {
  const query = useQuery({ queryKey: ['ticket-onu', ticketId], queryFn: ({ signal }) => ticketApi.ticketOnu(ticketId, signal), retry: false, staleTime: 30_000 })
  return <section className="detail-panel"><div className="detail-panel__heading"><h2>BDCOM ONU</h2></div>{query.isPending ? <p className="muted">Проверяем источник ONU…</p> : query.isError ? <Notice tone="error">{errorText(query.error)}</Notice> : <OnuCard result={query.data} />}</section>
}
