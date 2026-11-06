import { ShieldCheck } from 'lucide-react'
import type { CSSProperties } from 'react'
import { roleLabel } from '../layout/AppShell'
import type { MeResponse } from '../shared/api/types'

export function WorkspaceWelcome({ user }: { user: MeResponse['user'] }) {
  const nameWords = user.display_name.trim().split(/\s+/)
  let letterOffset = 0
  const nameContent = nameWords.map((word, wordIndex) => {
    const letters = Array.from(word)
    const animatedCount = Math.min(letters.length, Math.max(0, 24 - letterOffset))
    const animated = letters.slice(0, animatedCount).map((letter, letterIndex) => <span
      className="workspace-welcome__name-letter"
      key={letterIndex}
      style={{ '--letter-delay': `${(letterOffset + letterIndex) * 24 + 90}ms` } as CSSProperties}
    >{letter}</span>)
    letterOffset += animatedCount
    return <span className="workspace-welcome__name-word" key={wordIndex} aria-hidden="true">{animated}{letters.slice(animatedCount).join('')}</span>
  })

  return (
    <section className="workspace-welcome" aria-labelledby="workspace-welcome-title">
      <div className="workspace-welcome__glow" aria-hidden="true" />
      <div className="workspace-welcome__content">
        <h1 id="workspace-welcome-title" aria-label={`Добро пожаловать, ${user.display_name}`}>
          <span className="workspace-welcome__greeting">Добро пожаловать,</span>{' '}
          <span className="workspace-welcome__name">{nameContent}</span>
        </h1>
      </div>
      <div className="workspace-welcome__orbit" aria-hidden="true"><i /><i /><i /></div>
      <div className="role-chip"><ShieldCheck size={17} /> {roleLabel(user.role)}</div>
    </section>
  )
}
