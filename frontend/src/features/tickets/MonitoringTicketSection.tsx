import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import { ticketApi } from './ticket-api'
import type { TicketDetail } from './types'
import { MonitoringReport } from './MonitoringReport'
import { OnuCard } from './OnuCard'

export function MonitoringTicketSection({ ticket }: { ticket: TicketDetail }) {
  const queryClient = useQueryClient()
  const [saved, setSaved] = useState(false)
  const hasConnection = !!ticket.connection_snapshot_id
  const isEthernet = ticket.selected_connection?.access_type === 'ETHERNET'
  const onu = useQuery({
    queryKey: ['ticket-onu', ticket.id],
    queryFn: ({ signal }) => ticketApi.ticketOnu(ticket.id, signal),
    enabled: hasConnection && !isEthernet,
    retry: false,
    staleTime: 30_000,
  })
  const isPon = ticket.selected_connection?.access_type === 'EPON' || ticket.selected_connection?.access_type === 'GPON' || !!onu.data?.candidates.some((item) => item.pon_type === 'EPON' || item.pon_type === 'GPON')
  const check = useMutation({
    mutationFn: () => ticketApi.checkTicket(ticket.id, ticket.version),
    onSuccess: () => setSaved(false),
  })
  const confirm = useMutation({
    mutationFn: () => ticketApi.confirmCheck(ticket.id, check.data!.check_id, ticket.version),
    onSuccess: async () => {
      setSaved(true)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['ticket', ticket.id] }),
        queryClient.invalidateQueries({ queryKey: ['ticket-diagnostics', ticket.id] }),
        queryClient.invalidateQueries({ queryKey: ['ticket-history', ticket.id] }),
      ])
    },
  })
  if (!hasConnection) return null
  const monitoring = <>
    {ticket.status !== 'CLOSED' && <div className="monitoring-action"><Button variant="secondary" disabled={check.isPending || confirm.isPending} onClick={() => check.mutate()}>{check.isPending ? 'Проверяем…' : 'Проверить подключение'}</Button></div>}
    {check.isError && <Notice tone="error">{errorText(check.error)}</Notice>}
    {check.data && <MonitoringReport report={check.data.report} />}
    {check.data && ticket.status === 'IN_PROGRESS' && !saved && <Button variant="primary" disabled={confirm.isPending} onClick={() => confirm.mutate()}>{confirm.isPending ? 'Сохраняем…' : 'Подтвердить и сохранить в истории'}</Button>}
    {confirm.isError && <Notice tone="error">{errorText(confirm.error)}</Notice>}
    {saved && <Notice>Результат сохранён в истории заявки.</Notice>}
  </>
  return <section className="detail-panel">
    <div className="detail-panel__heading"><h2>Диагностика подключения</h2></div>
    {!isEthernet && (onu.isPending ? <p className="muted">Проверяем источник ONU…</p> : onu.isError ? <Notice tone="error">{errorText(onu.error)}</Notice> : <OnuCard result={onu.data} />)}
    {isPon ? <details className="diagnostic-details"><summary>Дополнительные проверки оборудования</summary>{monitoring}</details> : monitoring}
  </section>
}
