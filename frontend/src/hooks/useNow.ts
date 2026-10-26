import { useEffect, useState } from 'react'

/** Date.now() que se atualiza a cada `intervalMs` (linha da hora atual, horários passados). */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}
