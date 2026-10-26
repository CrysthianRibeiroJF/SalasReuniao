/** Medidas fixas da grade (px). A altura das linhas é calculada para caber na tela. */
export const TIME_COL_WIDTH = 58
export const MIN_ROOM_WIDTH = 128
export const GROUP_HEADER_HEIGHT = 26
export const ROOM_HEADER_HEIGHT = 50
export const HEADER_HEIGHT = GROUP_HEADER_HEIGHT + ROOM_HEADER_HEIGHT
export const MIN_ROW_HEIGHT = 22
export const MAX_ROW_HEIGHT = 44

export type RoomGroupKind = 'video' | 'normal'

export function cellKey(roomId: number, slot: number): string {
  return `${roomId}:${slot}`
}
