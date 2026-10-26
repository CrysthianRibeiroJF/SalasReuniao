/** Iniciais e cor determinística por usuário (avatares de presença e travas). */

// Tons escuros o bastante para texto branco (contraste ≥ 4.5:1).
const PALETTE = [
  '#3b4fd8', // ultramar
  '#0f766e', // petróleo
  '#b4235a', // framboesa
  '#7c3aed', // violeta
  '#a35a00', // caramelo
  '#2563a8', // azul aço
  '#4d7c0f', // oliva
  '#be3a1c', // telha
  '#6b4fa0', // ametista
  '#0e7490', // ciano escuro
] as const

export function userColor(userId: number): string {
  // Hash multiplicativo (Knuth) para espalhar ids consecutivos pela paleta.
  const index = (Math.imul(userId, 0x9e3779b1) >>> 0) % PALETTE.length
  return PALETTE[index]!
}

export function initials(fullName: string): string {
  const words = fullName
    .trim()
    .split(/\s+/)
    .filter((w) => w.length > 1 || /[A-Za-zÀ-ÿ]/.test(w))
    .filter((w) => !/^(da|de|do|das|dos|e)$/i.test(w))
  if (words.length === 0) return '?'
  const first = words[0]![0] ?? ''
  const last = words.length > 1 ? (words[words.length - 1]![0] ?? '') : (words[0]![1] ?? '')
  return (first + last).toUpperCase()
}

export function firstName(fullName: string): string {
  return fullName.trim().split(/\s+/)[0] ?? fullName
}
