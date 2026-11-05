import { useQuery } from '@tanstack/react-query'
import { errorText } from '../../shared/api/client'
import { settingsApi, type TemplatePurpose } from './settings-api'

const title: Record<TemplatePurpose, string> = {
  decision: 'Шаблон решения', comment: 'Шаблон комментария', callback: 'Шаблон обзвона',
}

export function TemplatePicker({ purpose, onInsert, maxLength, currentLength }: {
  purpose: TemplatePurpose
  onInsert: (text: string) => void
  maxLength: number
  currentLength: number
}) {
  const templates = useQuery({
    queryKey: ['ticket-text-templates', purpose],
    queryFn: ({ signal }) => settingsApi.templates(purpose, false, signal),
  })
  if (templates.isPending) return <p className="muted">Загружаем шаблоны…</p>
  if (templates.isError) return <p className="muted">Шаблоны недоступны: {errorText(templates.error)}</p>
  if (!templates.data.length) return null
  return <label className="field template-picker"><span>{title[purpose]}</span>
    <select aria-label={title[purpose]} defaultValue="" onChange={(event) => {
      const item = templates.data.find((entry) => entry.id === event.target.value)
      if (item && currentLength + item.body.length + (currentLength ? 1 : 0) <= maxLength) onInsert(item.body)
      event.target.value = ''
    }}>
      <option value="">Выберите для вставки</option>
      {templates.data.map((item) => <option key={item.id} value={item.id} disabled={currentLength + item.body.length + (currentLength ? 1 : 0) > maxLength}>{item.name}</option>)}
    </select>
  </label>
}
