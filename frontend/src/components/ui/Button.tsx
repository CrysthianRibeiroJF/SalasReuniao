import type { ButtonHTMLAttributes, ReactNode, Ref } from 'react'

import { cn } from '../../lib/cn'
import { Spinner } from './Spinner'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'video' | 'normal'
export type ButtonSize = 'xs' | 'sm' | 'md'

const VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-ink text-white hover:bg-ink-2 disabled:bg-ink/40 shadow-sm',
  secondary:
    'bg-surface text-ink border border-line-strong hover:border-muted/60 hover:bg-paper disabled:text-faint shadow-[0_1px_0_rgb(24_33_43/0.04)]',
  ghost: 'text-ink-2 hover:bg-ink/[0.06] disabled:text-faint',
  danger: 'bg-danger text-white hover:bg-danger-ink disabled:bg-danger/40 shadow-sm',
  video: 'bg-video-tint text-video-ink border border-video-soft hover:bg-video-soft disabled:opacity-50',
  normal: 'bg-normal-tint text-normal-ink border border-normal-soft hover:bg-normal-soft disabled:opacity-50',
}

const SIZES: Record<ButtonSize, string> = {
  xs: 'h-6 px-2 text-xs gap-1 rounded-[5px]',
  sm: 'h-8 px-2.5 text-[13px] gap-1.5 rounded-md',
  md: 'h-9 px-3.5 text-sm gap-2 rounded-md',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  icon?: ReactNode
  ref?: Ref<HTMLButtonElement>
}

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  icon,
  className,
  children,
  disabled,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        'inline-flex shrink-0 items-center justify-center font-medium whitespace-nowrap transition-colors select-none disabled:cursor-not-allowed',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner className="size-3.5" /> : icon}
      {children}
    </button>
  )
}
