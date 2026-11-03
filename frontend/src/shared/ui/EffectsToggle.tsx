import { setSimplifiedEffects, useSimplifiedEffects } from './effects-preference'

export function EffectsToggle() {
  const simplified = useSimplifiedEffects()
  return <label className="effects-toggle">
    <input type="checkbox" checked={simplified} onChange={(event) => setSimplifiedEffects(event.target.checked)} />
    <span>Упрощённые эффекты</span>
  </label>
}
