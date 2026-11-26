import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ApiError, errorText } from '../../shared/api/client'
import { Button } from '../../shared/ui/Button'
import { Notice } from '../../shared/ui/Notice'
import { formatDate } from './presentation'
import { ticketApi } from './ticket-api'
import type { FieldWorkHandoff } from './types'

export function FieldWorkEditor({ ticketId, handoff, address }: {
  ticketId: string; handoff: FieldWorkHandoff; address: string;
}) {
  const client = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [page, setPage] = useState(1)
  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')
  const [visitAddress, setVisitAddress] = useState('')
  const [extra, setExtra] = useState('')
  const [version, setVersion] = useState(1)
  const [requestId, setRequestId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const history = useQuery({
    queryKey: ['field-work-changes', handoff.id, page],
    queryFn: ({ signal }) => ticketApi.fieldWorkChanges(ticketId, handoff.id, page, signal),
    enabled: showHistory, refetchInterval: showHistory ? 15000 : false,
  })
  const editable = handoff.work_status === 'AWAITING_REPORT'
    && ['DRAFT', 'FAILED', 'PUBLISHED', 'SENT'].includes(handoff.delivery_status)
  function begin() {
    setReason(handoff.reason); setNote(handoff.note || '')
    setVisitAddress(handoff.visit_address ?? address); setExtra('')
    setVersion(handoff.details_version ?? 1); setRequestId(''); setError(null); setEditing(true)
  }
  async function save() {
    if (busy || !reason.trim()) return
    const id = requestId || crypto.randomUUID()
    setRequestId(id); setBusy(true); setError(null)
    try {
      await ticketApi.updateFieldDetails(ticketId, handoff.id, {
        reason: reason.trim(), note: note.trim() || null, visit_address: visitAddress.trim() || null,
        additional_information: extra.trim() || null, expected_version: version, client_request_id: id,
      })
      setEditing(false); setShowHistory(true); setPage(1)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['ticket-field-work', ticketId] }),
        client.invalidateQueries({ queryKey: ['ticket-history', ticketId] }),
        client.invalidateQueries({ queryKey: ['field-work-changes', handoff.id] }),
      ])
    } catch (cause) {
      setError(cause)
      if (cause instanceof ApiError && cause.status === 409) {
        await client.invalidateQueries({ queryKey: ['ticket-field-work', ticketId] })
      }
    } finally { setBusy(false) }
  }
  function changed() { setRequestId('') }
  return <div className="field-work-editor">
    <p className="ticket-description"><strong>Причина передачи:</strong> {handoff.reason}</p>
    {editable && !editing && <Button variant="quiet" onClick={begin}>Изменить / дополнить задание</Button>}
    {editing && <form className="ticket-action-form" onSubmit={(event) => { event.preventDefault(); void save() }}>
      <label className="field"><span>Причина выезда</span><textarea required rows={4} maxLength={10000} value={reason} onChange={(event) => { setReason(event.target.value); changed() }} /></label>
      <label className="field"><span>Адрес выезда</span><textarea rows={2} maxLength={500} value={visitAddress} onChange={(event) => { setVisitAddress(event.target.value); changed() }} /></label>
      <label className="field"><span>Примечание к заданию</span><textarea rows={3} maxLength={10000} value={note} onChange={(event) => { setNote(event.target.value); changed() }} /></label>
      <label className="field"><span>Дополнительные сведения</span><textarea aria-label="Дополнительные сведения" rows={3} maxLength={10000} value={extra} onChange={(event) => { setExtra(event.target.value); changed() }} /><small>Будут добавлены в историю и доступны монтажникам.</small></label>
      <p className="rail-note">Изменения появятся в OSS Field Work. Сохранённое сообщение в Telegram-группе останется прежним; монтажники получат уведомление.</p>
      {error !== null && <Notice tone="error">{errorText(error)}</Notice>}
      <div><Button type="submit" disabled={busy || !reason.trim()}>Сохранить задание</Button><Button type="button" variant="quiet" disabled={busy} onClick={() => setEditing(false)}>Отмена</Button></div>
    </form>}
    <Button variant="quiet" onClick={() => setShowHistory(!showHistory)} aria-expanded={showHistory}>Изменения и дополнения</Button>
    {showHistory && <div className="field-work-replies">
      {history.isPending && <p>Загружаем изменения…</p>}
      {history.isError && <Notice tone="error">{errorText(history.error)}</Notice>}
      {history.data?.total === 0 && <p className="rail-note">Изменений пока нет.</p>}
      {history.data?.items.map((entry) => <article key={entry.id}><div><strong>{entry.author}</strong><time>{formatDate(entry.created_at)}</time></div>
        {entry.before.reason !== entry.after.reason && <p>Причина: {entry.after.reason}</p>}
        {entry.before.visit_address !== entry.after.visit_address && <p>Адрес: {entry.after.visit_address || 'не указан'}</p>}
        {entry.before.note !== entry.after.note && <p>Примечание: {entry.after.note || 'не указано'}</p>}
        {entry.additional_information && <p>{entry.additional_information}</p>}
      </article>)}
      {history.data && history.data.total > 20 && <div className="fact-pager"><Button disabled={page === 1} onClick={() => setPage(page - 1)}>Новее</Button><span>{page}</span><Button disabled={page * 20 >= history.data.total} onClick={() => setPage(page + 1)}>Ранее</Button></div>}
    </div>}
  </div>
}
