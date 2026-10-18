import { ShieldCheck } from 'lucide-react'
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { roleLabel } from '../layout/AppShell'
import type { MeResponse } from '../shared/api/types'

const petals = [
  { kind: 'flower', x: -82, y: -45, turn: -30, delay: 0 },
  { kind: 'petal', x: -54, y: -75, turn: -60, delay: 45 },
  { kind: 'petal', x: -16, y: -62, turn: 35, delay: 80 },
  { kind: 'flower', x: 34, y: -70, turn: 28, delay: 35 },
  { kind: 'petal', x: 78, y: -35, turn: 75, delay: 95 },
  { kind: 'petal', x: 94, y: 12, turn: 135, delay: 25 },
  { kind: 'flower', x: 53, y: 48, turn: 45, delay: 75 },
  { kind: 'petal', x: 10, y: 66, turn: 160, delay: 105 },
  { kind: 'petal', x: -49, y: 56, turn: -115, delay: 55 },
  { kind: 'petal', x: -94, y: 15, turn: -75, delay: 115 },
] as const

export function WorkspaceWelcome({ user }: { user: MeResponse['user'] }) {
  const [burst, setBurst] = useState(0)
  const clearBurst = useRef<number | null>(null)
  const nameWords = user.display_name.trim().split(/\s+/)
  let letterOffset = 0
  const nameContent = nameWords.map((word, wordIndex) => <span className="workspace-welcome__name-word" key={wordIndex} aria-hidden="true">
    {Array.from(word).map((letter, letterIndex) => <span
      className="workspace-welcome__name-letter"
      key={letterIndex}
      style={{ '--letter-delay': `${Math.min(letterOffset++, 32) * 36 + 120}ms` } as CSSProperties}
    >{letter}</span>)}
  </span>)

  useEffect(() => () => {
    if (clearBurst.current !== null) window.clearTimeout(clearBurst.current)
  }, [])

  function bloom() {
    if (clearBurst.current !== null) window.clearTimeout(clearBurst.current)
    setBurst((value) => value + 1)
    clearBurst.current = window.setTimeout(() => {
      setBurst(0)
      clearBurst.current = null
    }, 1600)
  }

  return (
    <section className="workspace-welcome" aria-labelledby="workspace-welcome-title">
      <div className="workspace-welcome__glow" aria-hidden="true" />
      <div className="workspace-welcome__content">
        <h1 id="workspace-welcome-title" aria-label={`Добро пожаловать, ${user.display_name}`}>
          <span className="workspace-welcome__greeting">Добро пожаловать,</span>{' '}
          {user.role === 'operator'
            ? <button className="workspace-welcome__name workspace-welcome__name--interactive" type="button" onClick={bloom} title="Нажмите, чтобы появились цветы" aria-label={user.display_name}>
                {nameContent}
                {burst > 0 && <span className="workspace-welcome__burst" key={burst} aria-hidden="true">
                  {petals.map((petal, index) => <span
                    className={`workspace-welcome__particle workspace-welcome__particle--${petal.kind}`}
                    key={index}
                    style={{
                      '--petal-x': `${petal.x}px`,
                      '--petal-y': `${petal.y}px`,
                      '--petal-turn': `${petal.turn}deg`,
                      '--petal-delay': `${petal.delay}ms`,
                    } as CSSProperties}
                  >{petal.kind === 'flower' ? '🌸' : null}</span>)}
                </span>}
              </button>
            : <span className="workspace-welcome__name">{nameContent}</span>}
        </h1>
      </div>
      <div className="workspace-welcome__orbit" aria-hidden="true"><i /><i /><i /></div>
      <div className="role-chip"><ShieldCheck size={17} /> {roleLabel(user.role)}</div>
    </section>
  )
}
