import { Eye, EyeOff } from 'lucide-react'
import { useState } from 'react'

export function PasswordInput({
  label,
  name,
  value,
  onChange,
  autoComplete = 'new-password',
}: {
  label: string
  name: string
  value: string
  onChange: (value: string) => void
  autoComplete?: 'current-password' | 'new-password'
}) {
  const [visible, setVisible] = useState(false)
  return <label className="field password-field"><span>{label}</span><span className="password-field__control">
    <input
      type={visible ? 'text' : 'password'}
      name={name}
      autoComplete={autoComplete}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      maxLength={1024}
      required
    />
    <button type="button" className="password-field__toggle" aria-label={`${visible ? 'Скрыть' : 'Показать'}: ${label}`} onClick={() => setVisible(!visible)}>
      {visible ? <EyeOff size={16} /> : <Eye size={16} />}
    </button>
  </span></label>
}
