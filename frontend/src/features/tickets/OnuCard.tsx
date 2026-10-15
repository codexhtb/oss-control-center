import { useQuery } from '@tanstack/react-query'
import { errorText } from '../../shared/api/client'
import { Notice } from '../../shared/ui/Notice'
import { ticketApi } from './ticket-api'
import type { OnuResult } from './types'

const stateLabels = {
  ONLINE: 'Online', OFFLINE: 'Offline', DEREGISTERED: 'Deregistered', UNKNOWN: 'UNKNOWN',
}

function SourceTime({ label, value, retrievedAt }: { label: string; value: string | null; retrievedAt: string }) {
  // Compare calendar dates only: the source has no verified time zone.
  const date = value?.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}:\d{2}:\d{2})$/)
  const received = retrievedAt.match(/^(\d{4})-(\d{2})-(\d{2})/)
  const dayGap = date && received
    ? Math.floor((Date.UTC(+received[1]!, +received[2]! - 1, +received[3]!) - Date.UTC(+date[1]!, +date[2]! - 1, +date[3]!)) / 86_400_000)
    : 0
  return <div className="onu-source-time"><span>{label}</span><strong>{date ? `${date[3]}.${date[2]}.${date[1]} ${date[4]}` : value || 'Время неизвестно'}</strong>{dayGap >= 2 && <small title={`Разница календарных дат: ${dayGap} дн. Часовой пояс источника не подтверждён.`}>Ранее даты получения</small>}</div>
}

function OpticalValue({ label, value }: { label: string; value: string | null }) {
  return <div className="onu-optic" role="group" aria-label={label}><span>{label}</span><div>{value === null ? <span className="onu-optic__missing">Нет измерения</span> : <><strong>{value}</strong><span className="onu-optic__unit"> dBm</span></>}</div></div>
}

function Candidate({ item, vlan, retrievedAt }: { item: OnuResult['candidates'][number]; vlan: string | number | null | undefined; retrievedAt: string }) {
  const disconnected = item.state === 'OFFLINE' || item.state === 'DEREGISTERED'
  return <div className="onu-candidate">
    <div className="onu-candidate__heading"><div className="onu-identity"><span>OLT · {item.olt || 'Неизвестен'}</span><strong>{item.interface}</strong></div><div className="onu-state-context"><span>По опросу ONU</span><span className={`onu-state onu-state--${item.state.toLowerCase()}`}>{stateLabels[item.state]}</span></div></div>
    <div className="subscriber-facts subscriber-facts--three onu-identifiers">
      <div className="subscriber-fact"><span>MAC ONU</span><strong>{item.mac || 'Отсутствует'}</strong></div>
      <div className="subscriber-fact"><span>Serial</span><strong>{item.serial || 'Отсутствует'}</strong></div>
      <div className="subscriber-fact"><span>VLAN подключения · NetUP</span><strong>{vlan ?? 'Неизвестен'}</strong></div>
    </div>
    {disconnected && <p className="onu-disconnect-reason">Причина отключения: <strong>{item.reason && item.reason !== 'unknown' ? item.reason : 'Не указана'}</strong></p>}
    <div className="onu-optics" aria-label="Оптические показатели">
      <OpticalValue label="ONU Rx" value={item.onu_rx_dbm} />
      <OpticalValue label="OLT Rx" value={item.olt_rx_dbm} />
      <OpticalValue label="ONU Tx" value={item.onu_tx_dbm} />
    </div>
    <div className="onu-source-times">
      <SourceTime label="Наблюдение состояния ONU" value={item.inventory_polled_at} retrievedAt={retrievedAt} />
      <SourceTime label="Измерение оптики" value={item.optical_measured_at} retrievedAt={retrievedAt} />
    </div>
  </div>
}

export function OnuCard({ result, vlan }: { result: OnuResult; vlan?: string | null }) {
  const billingWarning = result.limitations.includes('netup_topology_stale') && <p className="onu-source-note">NetUP: историческая лабораторная копия.</p>
  if (result.availability === 'UNAVAILABLE') return <div className="onu-candidates"><Notice tone="error">Источник BDCOM ONU не подключён или недоступен.</Notice>{billingWarning}</div>
  if (!result.candidates.length) return <><p className="muted">ONU не сопоставлена с абонентом. {result.limitations.includes('olt_mapping_unconfirmed') ? 'Соответствие OLT не утверждено.' : result.limitations.includes('multiple_service_links_same_pon_vlan') ? 'Один OLT, PON-порт и VLAN указаны у нескольких сервисных связок.' : result.limitations.includes('onu_composite_key_not_found') ? 'Нет действующей ONU с таким OLT, PON-портом и VLAN.' : result.availability === 'CONFLICT' ? 'В NetUP есть противоречивые привязки.' : 'Подтверждённых данных нет.'}</p>{billingWarning}</>
  return <div className="onu-candidates">
    {result.association !== 'MATCHED_BY_RULE' && <p className="muted">Кандидаты для проверки инженером. Привязка к абоненту не подтверждена.</p>}
    {result.candidates.map((item) => <Candidate key={`${item.pon_type}-${item.interface}-${item.serial || item.mac}`} item={item} vlan={result.association === 'MATCHED_BY_RULE' ? result.match?.vlan ?? vlan : vlan} retrievedAt={result.retrieved_at} />)}
    <p className="onu-source-note">LibreNMS BDCOM · Время источника, часовой пояс не подтверждён.</p>
    {billingWarning}
    <details className="diagnostic-details"><summary>Источники и актуальность</summary>
      {result.association === 'MATCHED_BY_RULE' && result.match
        ? <p>Привязка: NetUP, порт {result.match.netup_port} → PON {result.match.pon_port}, VLAN {result.match.vlan}. Состояние и оптика: LibreNMS BDCOM. Данные NetUP: {result.match.billing_observed_at || 'время неизвестно'}.</p>
        : <p>Источник ONU: LibreNMS BDCOM. Привязка к абоненту не подтверждена.</p>}
      <p>Получено: {result.retrieved_at}. Состояние относится к опросу ONU, оптические значения — к своему измерению; их возраст может различаться.</p>
      {result.limitations.includes('onu_candidates_truncated') && <p>Показаны только первые 20 кандидатов.</p>}
      {result.limitations.includes('onu_composite_key_ambiguous') && <p>Несколько ONU с одинаковым OLT, PON-портом и VLAN; нужна проверка инженером.</p>}
    </details>
  </div>
}

export function SubscriberOnuCard({ lookupId, connectionId }: { lookupId: string; connectionId: string }) {
  const query = useQuery({ queryKey: ['subscriber-onu', lookupId, connectionId], queryFn: ({ signal }) => ticketApi.subscriberOnu(lookupId, connectionId, signal), retry: false, staleTime: 30_000 })
  return <div className="onu-section"><h4>BDCOM ONU</h4>{query.isPending ? <p className="muted">Проверяем источник ONU…</p> : query.isError ? <Notice tone="error">{errorText(query.error)}</Notice> : <OnuCard result={query.data} />}</div>
}
