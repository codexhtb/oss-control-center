import type { DiagnosticReport } from './types'
import { formatDate } from './presentation'

const quality: Record<string, string> = {
  KNOWN: 'Актуально', UNKNOWN: 'Неизвестно', STALE: 'Устарело',
  CONFLICT: 'Противоречие', UNAVAILABLE: 'Источник недоступен',
}
const result: Record<string, string> = {
  UP: 'Доступно', DOWN: 'Недоступно', UNREACHABLE: 'Недостижимо',
  OK: 'Норма', WARNING: 'Предупреждение', CRITICAL: 'Критично',
  UNKNOWN: 'Неизвестно', UNAVAILABLE: 'Источник недоступен',
}
const limitations: Record<string, string> = {
  device_mapping_unconfirmed: 'Соответствие устройства NetUP и мониторинга не подтверждено.',
  device_mapping_unique_exact_sysname: 'Устройство сопоставлено по уникальному точному совпадению имени в NetUP и LibreNMS.',
  conflicting_netup_switches: 'В биллинге указано несколько устройств.',
  conflicting_netup_ports: 'В биллинге указано несколько портов.',
  conflicting_netup_vlans: 'В биллинге указано несколько VLAN.',
  netup_switch_unknown: 'Устройство в биллинге не указано.',
  netup_port_unknown: 'Порт в биллинге не указан.',
  interface_mapping_unconfirmed: 'Соответствие порта NetUP и интерфейса не подтверждено.',
  mapped_interface_missing: 'Подтверждённый интерфейс не найден в LibreNMS.',
  librenms_unavailable: 'LibreNMS не ответил на запрос.',
  librenms_not_configured: 'LibreNMS не настроен.',
  icinga_unavailable: 'Icinga2 не ответила на запрос.',
  icinga_not_configured: 'Icinga2 не настроена.',
  monitoring_mapping_missing: 'Нет подтверждённого сопоставления с мониторингом.',
  optical_measurement_not_verified: 'Оптические параметры для этого подключения не подтверждены.',
}

export function isDiagnosticReport(value: unknown): value is DiagnosticReport {
  return typeof value === 'object' && value !== null && Array.isArray((value as DiagnosticReport).observations)
}

function FullMonitoringReport({ report }: { report: DiagnosticReport }) {
  return <div className="monitoring-report">
    <p className="muted">Проверка {formatDate(report.checked_at)} · устройство: {quality[report.device_match]} · интерфейс: {quality[report.interface_match]}</p>
    {report.observations.length ? <div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Источник</th><th>Объект</th><th>Результат</th><th>Измерено</th><th>Получено</th><th>Качество</th></tr></thead><tbody>
      {report.observations.map((item, index) => <tr key={`${item.source}-${item.measured_object}-${index}`}><td>{item.source}</td><td>{item.measured_object}</td><td>{result[item.result] || item.result}</td><td>{item.observed_at ? formatDate(item.observed_at) : 'Время неизвестно'}</td><td>{formatDate(item.retrieved_at)}</td><td>{quality[item.quality]}</td></tr>)}
    </tbody></table></div> : <p className="muted">Наблюдений мониторинга для этого подключения нет.</p>}
    {report.limitations.length > 0 && <ul className="monitoring-limitations">{report.limitations.map((item) => <li key={item}>{limitations[item] || 'Ограничение источника данных.'}</li>)}</ul>}
  </div>
}

export function MonitoringReport({ report, scope = 'connection' }: { report: DiagnosticReport; scope?: 'connection' | 'olt' }) {
  if (scope === 'connection') return <FullMonitoringReport report={report} />
  return <div className="monitoring-report monitoring-report--olt">
    <p className="muted">Проверка OLT: {formatDate(report.checked_at)}. Доступность устройства оценивается отдельно от состояния ONU.</p>
    {report.observations.length ? <div className="table-scroll"><table className="subscriber-table"><thead><tr><th>Источник</th><th>Объект проверки</th><th>Результат</th><th>Наблюдение</th></tr></thead><tbody>
      {report.observations.map((item, index) => <tr key={`${item.source}-${index}`}><td>{item.source}</td><td>{item.measured_object.startsWith('device:') || item.measured_object === 'host' || item.measured_object === 'mapped_device' || item.measured_object === 'mapped_host' ? 'Устройство OLT' : item.measured_object.startsWith('interface:') ? 'Интерфейс оборудования' : item.measured_object.startsWith('service:') ? 'Сервис мониторинга' : 'Объект мониторинга'}</td><td>{result[item.result] || 'Неизвестно'}{item.quality !== 'KNOWN' && <small className="monitoring-quality">{quality[item.quality]}</small>}</td><td>{item.observed_at ? formatDate(item.observed_at) : 'Время неизвестно'}</td></tr>)}
    </tbody></table></div> : <p className="muted">Наблюдений об OLT нет.</p>}
    <details className="diagnostic-details"><summary>Технические сведения проверки</summary><FullMonitoringReport report={report} /></details>
  </div>
}
