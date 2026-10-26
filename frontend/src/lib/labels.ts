import type { BookingStatus, ImportSheetStatus, LogAction, UserRole } from '../api/types'

export const ACTION_LABELS: Record<LogAction, string> = {
  CREATED: 'Criou',
  IMPORTED: 'Importou',
  UPDATED: 'Editou',
  MOVED_ROOM: 'Trocou de sala',
  RESCHEDULED: 'Mudou o horário',
  STATUS_CHANGED: 'Mudou o status',
  CANCELLED: 'Cancelou',
}

export const STATUS_LABELS: Record<BookingStatus, string> = {
  CONFIRMED: 'Confirmada',
  CHECKED_IN: 'Check-in feito',
  CANCELLED: 'Cancelada',
  NO_SHOW: 'Não compareceu',
}

export const ROLE_LABELS: Record<UserRole, string> = {
  ADMIN: 'Administração',
  RECEPTIONIST: 'Recepção',
}

export const IMPORT_STATUS_LABELS: Record<ImportSheetStatus, string> = {
  IMPORTED: 'Importada',
  DRY_RUN: 'Simulada',
  SKIPPED_PAST: 'Ignorada (data passada)',
  // Sem data, aba oculta, sem cabeçalho/coluna de horários, grande demais...: o motivo vem no aviso.
  SKIPPED_UNRECOGNIZED: 'Ignorada (veja o aviso)',
  ERROR: 'Erro',
}
