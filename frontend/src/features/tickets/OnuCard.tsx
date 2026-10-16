import { useQuery } from '@tanstack/react-query'
import { Copy, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { ApiError, errorText } from '../../shared/api/client'
import { Notice } from '../../shared/ui/Notice'
import { Button } from '../../shared/ui/Button'
import { ticketApi } from './ticket-api'
import type { OnuResult } from './types'
import { confirmedOnu, diagnosticSummary, sourceTime } from './onu-actions'

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
  return <div className="onu-source-time"><span>{label}</span><strong>{sourceTime(value)}</strong>{dayGap >= 2 && <small title={`Разница календарных дат: ${dayGap} дн. Часовой пояс источника не подтверждён.`}>Старое измерение</small>}</div>
}

function OpticalValue({ label, value }: { label: string; value: string | null }) {
  return <div className="onu-optic" role="group" aria-label={label}><span>{label}</span><div>{value === null ? <span className="onu-optic__missing">Нет измерения</span> : <><strong>{value}</strong><span className="onu-optic__unit"> dBm</span></>}</div></div>
}

function Candidate({ item, vlan, retrievedAt, onCopy }: { item: OnuResult['candidates'][number]; vlan: string | number | null | undefined; retrievedAt: string; onCopy?: (label: string, value: string) => void }) {
  const disconnected = item.state === 'OFFLINE' || item.state === 'DEREGISTERED'
  const copy = (label: string, value: string | number | null | undefined) => value != null && String(value).trim() && !['UNKNOWN', 'UNAVAILABLE', 'CONFLICT'].includes(String(value).trim().toUpperCase()) && onCopy
    ? <button type="button" className="onu-copy" aria-label={`Скопировать ${label}`} title={`Скопировать ${label}`} onClick={() => onCopy(label, String(value))}><Copy size={13} aria-hidden="true" /></button> : null
  return <div className="onu-candidate">
    <div className="onu-candidate__heading"><div className="onu-identity"><span>OLT · {item.olt || 'Неизвестен'}{copy('OLT', item.olt)}</span><strong>{item.interface}{copy('интерфейс ONU', item.interface)}</strong></div><div className="onu-state-context"><span>По опросу ONU</span><span className={`onu-state onu-state--${item.state.toLowerCase()}`}>{stateLabels[item.state]}</span></div></div>
    <div className="subscriber-facts subscriber-facts--three onu-identifiers">
      <div className="subscriber-fact"><span>MAC ONU</span><strong>{item.mac || 'Отсутствует'}{copy('MAC ONU', item.mac)}</strong></div>
      <div className="subscriber-fact"><span>Serial</span><strong>{item.serial || 'Отсутствует'}{copy('Serial ONU', item.serial)}</strong></div>
      <div className="subscriber-fact"><span>VLAN подключения · NetUP</span><strong>{vlan ?? 'Неизвестен'}{copy('VLAN', vlan)}</strong></div>
    </div>
    {disconnected && <p className="onu-disconnect-reason">Причина отключения: <strong>{item.reason && item.reason !== 'unknown' ? item.reason : 'Не указана'}</strong></p>}
    <div className="onu-optics" aria-label="Оптические показатели">
      <OpticalValue label="ONU Rx" value={item.onu_rx_dbm} />
      <OpticalValue label="OLT Rx" value={item.olt_rx_dbm} />
      <OpticalValue label="ONU Tx" value={item.onu_tx_dbm} />
    </div>
    {disconnected && item.disconnection_at && <p className="onu-disconnect-time">{item.disconnection_time_basis === 'EPON_DEREGISTRATION' ? 'Время отключения' : 'Отключение обнаружено'}: <strong>{sourceTime(item.disconnection_at)}</strong></p>}
    <div className="onu-source-times"><SourceTime label="Оптика измерена" value={item.optical_measured_at} retrievedAt={retrievedAt} /></div>
  </div>
}

export function OnuCard({ result, vlan, onRefresh, refreshing = false }: { result: OnuResult; vlan?: string | null; onRefresh?: () => Promise<OnuResult>; refreshing?: boolean }) {
  const [feedback, setFeedback] = useState('')
  const [refreshBusy, setRefreshBusy] = useState(false)
  const refreshLock = useRef(false)
  useEffect(() => { if (!feedback) return; const timer = window.setTimeout(() => setFeedback(''), 3000); return () => window.clearTimeout(timer) }, [feedback])
  const confirmed = confirmedOnu(result)
  async function copy(label: string, value: string) {
    try {
      await navigator.clipboard.writeText(value)
      setFeedback(`${label}: Скопировано`)
    } catch { setFeedback('Не удалось скопировать. Проверьте доступ к буферу обмена.') }
  }
  async function refresh() {
    if (!onRefresh || refreshLock.current || refreshing) return
    refreshLock.current = true
    setRefreshBusy(true)
    setFeedback('')
    try {
      const updated = await onRefresh()
      setFeedback(updated.availability === 'UNAVAILABLE'
        ? 'Источник недоступен. Не удалось обновить данные ONU.'
        : 'Данные обновлены. Время опроса и измерения указано ниже.')
    }
    catch { setFeedback('Источник недоступен. Не удалось обновить данные ONU.') }
    finally { refreshLock.current = false; setRefreshBusy(false) }
  }
  return <div className={`onu-candidates${confirmed ? ' onu-candidates--matched' : ''}`}>
    {onRefresh && <div className="onu-quick-actions"><Button type="button" variant="quiet" icon={<RefreshCw size={14} />} disabled={refreshing || refreshBusy} onClick={() => void refresh()}>{refreshing || refreshBusy ? 'Обновляем…' : 'Обновить данные'}</Button>{confirmed && result.match && <Button type="button" variant="quiet" icon={<Copy size={14} />} onClick={() => void copy('Сводка', diagnosticSummary(confirmed, result.match!.vlan))}>Скопировать сводку</Button>}<span role="status" aria-live="polite">{feedback}</span></div>}
    {result.availability === 'UNAVAILABLE' ? <Notice tone="error">Источник BDCOM ONU не подключён или недоступен.</Notice> : !result.candidates.length ? <p className="muted">ONU не сопоставлена с абонентом. {result.limitations.includes('olt_mapping_unconfirmed') ? 'Соответствие OLT не утверждено.' : result.limitations.includes('multiple_service_links_same_pon_vlan') ? 'Один OLT, PON-порт и VLAN указаны у нескольких сервисных связок.' : result.limitations.includes('onu_composite_key_not_found') ? 'Нет действующей ONU с таким OLT, PON-портом и VLAN.' : result.availability === 'CONFLICT' ? 'В NetUP есть противоречивые привязки.' : 'Подтверждённых данных нет.'}</p> : <>
    {result.association !== 'MATCHED_BY_RULE' && <p className="muted">Кандидаты для проверки инженером. Привязка к абоненту не подтверждена.</p>}
    {result.candidates.map((item) => <Candidate key={`${item.pon_type}-${item.interface}-${item.serial || item.mac}`} item={item} vlan={result.association === 'MATCHED_BY_RULE' ? result.match?.vlan ?? vlan : vlan} retrievedAt={result.retrieved_at} onCopy={confirmed === item ? (label, value) => void copy(label, value) : undefined} />)}
    {result.limitations.includes('onu_candidates_truncated') && <p className="muted">Показаны первые 20 кандидатов.</p>}
    </>}
  </div>
}

export function SubscriberOnuCard({ lookupId, connectionId }: { lookupId: string; connectionId: string }) {
  const query = useQuery({ queryKey: ['subscriber-onu', lookupId, connectionId], queryFn: ({ signal }) => ticketApi.subscriberOnu(lookupId, connectionId, signal), retry: false, staleTime: 30_000 })
  const denied = query.error instanceof ApiError && [401, 403].includes(query.error.status)
  return <div className="onu-section"><h4>BDCOM ONU</h4>{query.isPending ? <p className="muted">Проверяем источник ONU…</p> : query.isError && (!query.data || denied) ? <><Notice tone="error">{errorText(query.error)}</Notice>{!denied && <Button variant="quiet" onClick={() => void query.refetch()}>Обновить данные</Button>}</> : <OnuCard result={query.data!} onRefresh={async () => (await query.refetch({ throwOnError: true })).data!} refreshing={query.isFetching} />}</div>
}
