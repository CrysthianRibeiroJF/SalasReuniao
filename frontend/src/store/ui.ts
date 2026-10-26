import { create } from 'zustand'

import type { HHMM, ISODate, PeriodType } from '../api/types'
import type { RoomFilter } from '../lib/rooms'

/** Seleção de células numa coluna (índices de slot, âncora e foco inclusivos). */
export interface Selection {
  roomId: number
  date: ISODate
  anchor: number
  focus: number
}

export interface GridCursor {
  roomId: number
  slot: number
}

export interface AnchorRect {
  top: number
  left: number
  width: number
  height: number
}

/** Rascunho do modal rápido de agendamento (criação ou edição). */
export interface ComposerState {
  /** Identifica cada abertura do modal (o formulário reinicia quando muda). */
  key: number
  mode: 'create' | 'edit'
  bookingId: string | null
  roomId: number
  date: ISODate
  period: PeriodType
  start: HHMM
  end: HHMM
  /** Trava obtida ao selecionar (o modal assume a renovação e a liberação). */
  lockId: string | null
  anchor: AnchorRect | null
}

export interface PopoverState {
  bookingId: string
  anchor: AnchorRect
}

/** Posição otimista de uma reserva arrastada enquanto o PATCH não responde. */
export interface PendingMove {
  roomId: number
  start: HHMM
  end: HHMM
}

interface UiState {
  /** Espelho de ?date= (usado pelo tempo real para recarregar o dia exibido). */
  activeDate: ISODate | null
  filter: RoomFilter
  selection: Selection | null
  cursor: GridCursor | null
  composer: ComposerState | null
  popover: PopoverState | null
  highlightId: string | null
  pendingMoves: Record<string, PendingMove>
  /** Incrementado para pedir foco na busca (atalhos / e Ctrl+K). */
  searchFocusSeq: number

  setActiveDate: (date: ISODate) => void
  setFilter: (filter: RoomFilter) => void
  setSelection: (selection: Selection | null) => void
  setCursor: (cursor: GridCursor | null) => void
  openComposer: (composer: Omit<ComposerState, 'key'>) => void
  patchComposer: (patch: Partial<ComposerState>) => void
  closeComposer: () => void
  openPopover: (popover: PopoverState) => void
  closePopover: () => void
  highlight: (bookingId: string | null) => void
  setPendingMove: (bookingId: string, move: PendingMove | null) => void
  focusSearch: () => void
  reset: () => void
}

const initial = {
  activeDate: null,
  filter: 'all' as RoomFilter,
  selection: null,
  cursor: null,
  composer: null,
  popover: null,
  highlightId: null,
  pendingMoves: {},
  searchFocusSeq: 0,
}

let composerSeq = 0

export const useUi = create<UiState>()((set) => ({
  ...initial,
  setActiveDate: (activeDate) => set({ activeDate }),
  setFilter: (filter) => set({ filter }),
  setSelection: (selection) => set({ selection }),
  setCursor: (cursor) => set({ cursor }),
  openComposer: (composer) => set({ composer: { ...composer, key: ++composerSeq }, popover: null }),
  patchComposer: (patch) => set((s) => (s.composer ? { composer: { ...s.composer, ...patch } } : s)),
  closeComposer: () => set({ composer: null, selection: null }),
  openPopover: (popover) => set({ popover }),
  closePopover: () => set({ popover: null }),
  highlight: (highlightId) => set({ highlightId }),
  setPendingMove: (bookingId, move) =>
    set((s) => {
      const pendingMoves = { ...s.pendingMoves }
      if (move) pendingMoves[bookingId] = move
      else delete pendingMoves[bookingId]
      return { pendingMoves }
    }),
  focusSearch: () => set((s) => ({ searchFocusSeq: s.searchFocusSeq + 1 })),
  reset: () => set(initial),
}))
