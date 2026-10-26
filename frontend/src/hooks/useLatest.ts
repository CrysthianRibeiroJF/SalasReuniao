import { useLayoutEffect, useRef } from 'react'

/** Ref sempre com o valor mais recente (para callbacks usados dentro de efeitos/listeners). */
export function useLatest<T>(value: T): { readonly current: T } {
  const ref = useRef(value)
  useLayoutEffect(() => {
    ref.current = value
  })
  return ref
}
