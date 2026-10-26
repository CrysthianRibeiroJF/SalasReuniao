import type { ReactNode } from 'react'

import { cn } from '../../lib/cn'

export type BadgeTone = 'neutral' | 'video' | 'normal' | 'lock' | 'danger' | 'success' | 'warning' | 'ink'

const TONES: Record<BadgeTone, string> = {
  neutral: 'bg-paper text-ink-2 border-line-strong',
  video: 'bg-video-tint text-video-ink border-video-soft',
  normal: 'bg-normal-tint text-normal-ink border-normal-soft',
  lock: 'bg-lock-tint text-lock-ink border-lock/30',
  danger: 'bg-danger-tint text-danger-ink border-danger/25',
  success: 'bg-success-tint text-success border-success/25',
  warning: 'bg-warning-tint text-warning border-warning/30',
  ink: 'bg-ink text-white border-ink',
}

export function Badge({
  tone = 'neutral',
  icon,
  children,
  className,
  title,
}: {
  tone?: BadgeTone
  icon?: ReactNode
  children?: ReactNode
  className?: string
  title?: string
}) {
  return (
    <span
      title={title}
      className={cn(
        'inline-flex max-w-full items-center gap-1 rounded-[4px] border px-1.5 py-px text-[11px] leading-4 font-medium whitespace-nowrap',
        TONES[tone],
        className,
      )}
    >
      {icon}
      {children !== undefined && <span className="truncate">{children}</span>}
    </span>
  )
}
