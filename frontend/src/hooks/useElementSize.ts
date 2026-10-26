import { useCallback, useLayoutEffect, useState } from 'react'

export interface Size {
  width: number
  height: number
}

/**
 * Tamanho (client) de um elemento, atualizado por ResizeObserver.
 * Devolve um ref de callback: funciona mesmo se o elemento só aparecer depois (ex.: após um esqueleto).
 */
export function useElementSize<T extends HTMLElement>(): [(element: T | null) => void, Size] {
  const [element, setElement] = useState<T | null>(null)
  const [size, setSize] = useState<Size>({ width: 0, height: 0 })
  const ref = useCallback((el: T | null) => setElement(el), [])

  useLayoutEffect(() => {
    if (!element) return
    const update = () =>
      setSize((s) =>
        s.width === element.clientWidth && s.height === element.clientHeight
          ? s
          : { width: element.clientWidth, height: element.clientHeight },
      )
    update()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [element])

  return [ref, size]
}
