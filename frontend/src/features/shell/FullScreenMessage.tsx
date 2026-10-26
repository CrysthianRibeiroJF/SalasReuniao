import type { ReactNode } from 'react'

import { Spinner } from '../../components/ui/Spinner'

export function FullScreenMessage({
  title,
  description,
  action,
  loading,
}: {
  title: string
  description?: ReactNode
  action?: ReactNode
  loading?: boolean
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 bg-paper px-6 text-center">
      {loading && <Spinner className="size-5 text-muted" />}
      <p className="text-[15px] font-semibold text-ink">{title}</p>
      {description && <p className="max-w-md text-[13px] text-muted">{description}</p>}
      {action}
    </div>
  )
}
