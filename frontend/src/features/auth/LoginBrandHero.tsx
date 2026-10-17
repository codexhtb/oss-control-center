export function LoginBrandHero() {
  return (
    <section className="login-hero" aria-labelledby="login-brand-title">
      <div className="login-hero__aura" aria-hidden="true" />
      <div className="login-hero__depth login-hero__depth--back" aria-hidden="true" />
      <div className="login-hero__depth login-hero__depth--front" aria-hidden="true" />
      <div className="login-hero__mark-stage" aria-hidden="true">
        <svg className="login-hero__mark" viewBox="0 0 580 270" focusable="false">
          <defs>
            <linearGradient id="oss-mark-face" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#c8f9e9" />
              <stop offset="0.42" stopColor="#7de0c3" />
              <stop offset="1" stopColor="#3ea98e" />
            </linearGradient>
            <linearGradient id="oss-mark-edge" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#245f54" />
              <stop offset="1" stopColor="#12372f" />
            </linearGradient>
          </defs>
          <g className="login-hero__module login-hero__module--o">
            <circle className="login-hero__edge" cx="142" cy="135" r="72" pathLength="1" />
            <circle className="login-hero__face" cx="142" cy="135" r="72" pathLength="1" />
          </g>
          <g className="login-hero__module login-hero__module--c1">
            <path className="login-hero__edge" d="M 349 79 A 72 72 0 1 0 349 191" pathLength="1" />
            <path className="login-hero__face" d="M 349 79 A 72 72 0 1 0 349 191" pathLength="1" />
          </g>
          <g className="login-hero__module login-hero__module--c2">
            <path className="login-hero__edge" d="M 502 79 A 72 72 0 1 0 502 191" pathLength="1" />
            <path className="login-hero__face" d="M 502 79 A 72 72 0 1 0 502 191" pathLength="1" />
          </g>
          <circle className="login-hero__node login-hero__node--one" cx="214" cy="135" r="6" />
          <circle className="login-hero__node login-hero__node--two" cx="367" cy="135" r="6" />
          <circle className="login-hero__node login-hero__node--three" cx="520" cy="135" r="6" />
        </svg>
      </div>
      <div className="login-hero__caption">
        <p>OSS Control Center</p>
        <h1 id="login-brand-title">Оптические системы связи</h1>
      </div>
    </section>
  )
}
