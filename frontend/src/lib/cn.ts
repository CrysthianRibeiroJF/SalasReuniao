/** Junta classes condicionais: cn('a', cond && 'b', undefined) → "a b". */
export function cn(...classes: (string | false | null | undefined | 0)[]): string {
  return classes.filter(Boolean).join(' ')
}
