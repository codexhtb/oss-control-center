import { CircleAlert, Info } from 'lucide-react'
import type { ReactNode } from 'react'

export function Notice({ children, tone = 'info' }: { children: ReactNode; tone?: 'info' | 'error' }) {
  const Icon = tone === 'error' ? CircleAlert : Info
  return (
    <div className={`notice notice--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <Icon size={18} aria-hidden="true" />
      <div>{children}</div>
    </div>
  )
}
