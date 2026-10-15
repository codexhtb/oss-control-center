import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'quiet'
  icon?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button({ variant = 'secondary', icon, className = '', children, ...props }, ref) {
  return (
    <button ref={ref} className={`button button--${variant} ${className}`} {...props}>
      {icon && <span className="button__icon" aria-hidden="true">{icon}</span>}
      {children}
    </button>
  )
})
