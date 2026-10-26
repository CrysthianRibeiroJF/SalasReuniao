/**
 * Início de uma reserva a partir da seleção (clique, arrasto ou Enter):
 * trava a faixa via LOCK_SLOT e só então abre o modal. Trava negada = aviso e nada abre.
 * Sem tempo real, o modal abre mesmo assim (a constraint do banco continua protegendo).
 */
import type { HHMM, ISODate } from '../../api/types'
import { getRealtime } from '../../realtime/service'
import { toast } from '../../store/toasts'
import { type AnchorRect, useUi } from '../../store/ui'

export interface BeginBookingInput {
  roomId: number
  date: ISODate
  start: HHMM
  end: HHMM
  anchor: AnchorRect | null
}

let attempt = 0
/** Tentativa esperando a resposta do LOCK_SLOT (null = nenhuma). */
let waiting: number | null = null

export async function beginBooking(input: BeginBookingInput): Promise<boolean> {
  const current = ++attempt
  const ui = useUi.getState()
  ui.closePopover()
  const realtime = getRealtime()
  let lockId: string | null = null

  if (realtime?.isOpen) {
    waiting = current
    let result: Awaited<ReturnType<typeof realtime.lockSlot>>
    try {
      result = await realtime.lockSlot({
        room_id: input.roomId,
        date: input.date,
        start: input.start,
        end: input.end,
      })
    } finally {
      if (waiting === current) waiting = null
    }
    if (current !== attempt) {
      // Outra seleção começou enquanto esperávamos: esta trava não serve mais.
      if (result.ok) realtime.unlock(result.lock.lock_id)
      return false
    }
    // Negada por outra pessoa ou por regra (fora do horário, sala inativa): não abre o modal.
    // Falha do próprio servidor de travas (UNAVAILABLE): segue sem trava; o banco ainda protege.
    if (!result.ok && result.reason === 'DENIED' && result.code !== 'UNAVAILABLE') {
      useUi.getState().setSelection(null)
      toast.warning(result.message || 'Outra pessoa está reservando este horário agora.')
      return false
    }
    if (result.ok) lockId = result.lock.lock_id
  }

  useUi.getState().openComposer({
    mode: 'create',
    bookingId: null,
    roomId: input.roomId,
    date: input.date,
    period: 'CUSTOM',
    start: input.start,
    end: input.end,
    lockId,
    anchor: input.anchor,
  })
  return true
}

/** Invalida um beginBooking em andamento (ex.: Esc antes da resposta da trava). */
export function cancelPendingBegin(): void {
  attempt++
  waiting = null
}

/** Há uma seleção esperando a resposta da trava (o modal ainda vai abrir)? */
export function hasPendingBegin(): boolean {
  return waiting !== null
}
