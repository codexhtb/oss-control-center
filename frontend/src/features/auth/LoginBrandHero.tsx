import { useReducedMotion, useSimplifiedEffects } from '../../shared/ui/effects-preference'

function limitedDevice(): boolean {
  if (typeof navigator === 'undefined') return false
  const memory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory
  return (memory !== undefined && memory <= 2) ||
    (navigator.hardwareConcurrency > 0 && navigator.hardwareConcurrency <= 2)
}

const sPath = 'M 338 86 C 319 67 278 66 258 84 C 237 102 238 124 274 135 L 311 146 C 346 157 347 180 325 196 C 303 211 266 207 246 187'

function LetterS({ second = false }: { second?: boolean }) {
  return <g className={`login-hero__piece ${second ? 'login-hero__piece--third' : 'login-hero__piece--second'}`}>
    <g transform={second ? 'translate(160 0)' : undefined}>
      <path className="login-hero__extrusion" d={sPath} transform="translate(7 10)" />
      <path className="login-hero__bevel" d={sPath} />
      <path className="login-hero__face" d={sPath} />
      <path className="login-hero__glint" d="M 338 86 C 319 67 278 66 258 84" />
    </g>
  </g>
}

export function LoginBrandHero() {
  const simplified = useSimplifiedEffects()
  const reduced = useReducedMotion()
  const lite = simplified || reduced || limitedDevice()

  return <section className="login-hero" data-performance={lite ? 'lite' : 'full'} aria-labelledby="login-brand-title">
    <div className="login-hero__ambient" aria-hidden="true" />
    <div className="login-hero__mark-stage" aria-hidden="true">
      <svg className="login-hero__mark" viewBox="0 0 560 280" focusable="false">
        <defs>
          <linearGradient id="oss-brand-bevel" x1="12%" y1="0%" x2="90%" y2="100%">
            <stop offset="0" stopColor="#a6eed4" />
            <stop offset="0.25" stopColor="#286b5b" />
            <stop offset="0.68" stopColor="#16473f" />
            <stop offset="1" stopColor="#8bd9bc" />
          </linearGradient>
          <linearGradient id="oss-brand-face" x1="8%" y1="2%" x2="92%" y2="98%">
            <stop offset="0" stopColor="#e2fff0" />
            <stop offset="0.27" stopColor="#90e8c8" />
            <stop offset="0.63" stopColor="#3ba989" />
            <stop offset="1" stopColor="#1c6a59" />
          </linearGradient>
          <linearGradient id="oss-brand-floor" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0" stopColor="#68c6a5" stopOpacity=".33" />
            <stop offset="1" stopColor="#68c6a5" stopOpacity="0" />
          </linearGradient>
        </defs>
        <ellipse className="login-hero__floor" cx="282" cy="244" rx="222" ry="17" fill="url(#oss-brand-floor)" />
        <g className="login-hero__piece login-hero__piece--first">
          <circle className="login-hero__extrusion" cx="114" cy="135" r="65" transform="translate(7 10)" />
          <circle className="login-hero__bevel" cx="114" cy="135" r="65" />
          <circle className="login-hero__face" cx="114" cy="135" r="65" />
          <path className="login-hero__glint" d="M 69 88 A 65 65 0 0 1 155 84" />
        </g>
        <LetterS />
        <LetterS second />
      </svg>
    </div>
    <div className="login-hero__caption">
      <p>OSS Control Center</p>
      <h1 id="login-brand-title">Оптические системы связи</h1>
    </div>
  </section>
}
