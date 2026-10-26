/**
 * Espelho de backend/app/schemas.py e backend/app/realtime/events.py (contrato §5–§6).
 *
 * Convenções: datas locais "YYYY-MM-DD", horas locais "HH:MM" (fuso de ConfigOut.timezone),
 * instantes em ISO 8601 com offset. Erros de negócio: {"detail": {"code", "message", ...}};
 * erros de validação do FastAPI (422): {"detail": [ValidationErrorItem]}.
 */

export type ISODate = string
export type HHMM = string
export type ISODateTime = string

// ------------------------------------------------------------------ enums

export type UserRole = 'ADMIN' | 'RECEPTIONIST'
export type RoomType = 'VIDEO' | 'NORMAL'
export type VideoStatus = 'OPERATIONAL' | 'MAINTENANCE'
export type BookingStatus = 'CONFIRMED' | 'CHECKED_IN' | 'CANCELLED' | 'NO_SHOW'
export type PeriodType = 'CUSTOM' | 'FULL_DAY' | 'MORNING' | 'AFTERNOON'
export type LogAction =
  'CREATED' | 'IMPORTED' | 'UPDATED' | 'MOVED_ROOM' | 'RESCHEDULED' | 'STATUS_CHANGED' | 'CANCELLED'

// ------------------------------------------------------------------ erros

export interface ErrorDetail {
  code: string
  message: string
}

/** Códigos de erro de negócio documentados no contrato (o backend pode enviar outros). */
export type BusinessErrorCode =
  | 'INVALID_CREDENTIALS'
  /** 401: token ausente, inválido ou vencido. */
  | 'UNAUTHORIZED'
  /** 401: a senha mudou depois que o token foi emitido (token_version). */
  | 'SESSION_REVOKED'
  /** 401: usuário desativado (ou removido). */
  | 'ACCOUNT_DISABLED'
  /** 429 do login: tentativas demais; `retry_after_seconds`. */
  | 'TOO_MANY_ATTEMPTS'
  | 'FORBIDDEN'
  | 'USERNAME_TAKEN'
  | 'CANNOT_DEMOTE_SELF'
  | 'ROOM_NOT_FOUND'
  | 'ROOM_NAME_TAKEN'
  | 'ROOM_INACTIVE'
  /** 409 ao desativar sala com reservas ativas que ainda não terminaram (`count`, `bookings`). */
  | 'ROOM_HAS_FUTURE_BOOKINGS'
  | 'NOT_A_VIDEO_ROOM'
  | 'BOOKING_NOT_FOUND'
  | 'BOOKING_NOT_EDITABLE'
  | 'BOOKING_CONFLICT'
  | 'SLOT_LOCKED'
  | 'STALE_VERSION'
  | 'OUTSIDE_OPERATING_HOURS'
  | 'INVALID_TIME'
  | 'INVALID_INTERVAL'

/** Códigos gerados pelo próprio cliente quando a resposta não traz um código de negócio. */
export type ClientErrorCode = 'VALIDATION_ERROR' | 'NETWORK_ERROR' | 'SERVER_ERROR' | 'NOT_FOUND' | 'HTTP_ERROR'

export type ErrorCode = BusinessErrorCode | ClientErrorCode

export interface BusinessErrorBody {
  detail: ErrorDetail & Record<string, unknown>
}

export interface ValidationErrorItem {
  loc: (string | number)[]
  msg: string
  type: string
  input?: unknown
  ctx?: Record<string, unknown>
}

export interface ValidationErrorBody {
  detail: ValidationErrorItem[]
}

export interface BookingConflictDetail extends ErrorDetail {
  code: 'BOOKING_CONFLICT'
  conflicts: BookingOut[]
  alternatives: RoomBrief[]
}

export interface RoomHasFutureBookingsDetail extends ErrorDetail {
  code: 'ROOM_HAS_FUTURE_BOOKINGS'
  /** Total de reservas que impedem a desativação. */
  count: number
  /** Até 50 delas, por início. */
  bookings: BookingOut[]
}

export interface SlotLockedDetail extends ErrorDetail {
  code: 'SLOT_LOCKED'
  locks: LockOut[]
}

export interface StaleVersionDetail extends ErrorDetail {
  code: 'STALE_VERSION'
  current: BookingOut
}

export type WarningCode = 'VIDEO_ROOM_NOT_NEEDED' | 'VIDEO_UNAVAILABLE' | 'OVER_CAPACITY'

/** Aviso não bloqueante de criação/edição. `data` depende do código (ver WarningData). */
export interface BusinessWarning {
  code: WarningCode
  message: string
  data: Record<string, unknown>
}

export interface WarningData {
  VIDEO_ROOM_NOT_NEEDED: { free_normal_rooms: RoomBrief[] }
  VIDEO_UNAVAILABLE: { free_video_rooms: RoomBrief[] }
  OVER_CAPACITY: { capacity: number; attendees: number }
}

// ------------------------------------------------------------------ usuários / auth

export interface UserBrief {
  id: number
  full_name: string
}

export interface UserOut {
  id: number
  username: string
  full_name: string
  role: UserRole
  is_active: boolean
  created_at: ISODateTime
  last_login_at: ISODateTime | null
}

export interface LoginIn {
  username: string
  password: string
}

export interface TokenOut {
  access_token: string
  token_type: 'bearer'
  expires_at: ISODateTime
  user: UserOut
}

export interface UserCreate {
  username: string
  full_name: string
  password: string
  role?: UserRole
}

export interface UserUpdate {
  full_name?: string | null
  password?: string | null
  role?: UserRole | null
  is_active?: boolean | null
}

// ------------------------------------------------------------------ salas

export interface RoomBrief {
  id: number
  name: string
  room_type: RoomType
  effective_type: RoomType
  capacity: number
}

export interface RoomOut {
  id: number
  name: string
  /** Tipo original da sala. */
  room_type: RoomType
  /** VIDEO só se room_type=VIDEO e vídeo OPERATIONAL (RF02). */
  effective_type: RoomType
  capacity: number
  resources: string[]
  video_status: VideoStatus
  maintenance_ticket_id: string | null
  maintenance_ticket_url: string | null
  maintenance_since: ISODateTime | null
  display_order: number
  is_active: boolean
}

export interface RoomCreate {
  name: string
  room_type: RoomType
  capacity?: number
  resources?: string[]
  /** null = vai para o fim do grupo */
  display_order?: number | null
  is_active?: boolean
}

export interface RoomUpdate {
  name?: string | null
  room_type?: RoomType | null
  capacity?: number | null
  resources?: string[] | null
  display_order?: number | null
  is_active?: boolean | null
}

export interface MaintenanceIn {
  ticket_id: string
}

// ------------------------------------------------------------------ reservas

export interface BookingCreate {
  room_id: number
  date: ISODate
  period_type?: PeriodType
  /** Obrigatório se CUSTOM; ignorado nos presets. */
  start?: HHMM | null
  end?: HHMM | null
  ticket_number?: string | null
  title?: string | null
  requester_name: string
  requires_video?: boolean
  attendees?: number | null
  notes?: string | null
  /** Soft lock que o cliente segura para esta seleção (liberado ao salvar). */
  lock_id?: string | null
}

/** Edição/remanejamento: envie só o que muda + a `version` que o cliente tem (null = limpar). */
export interface BookingUpdate {
  version: number
  room_id?: number | null
  date?: ISODate | null
  period_type?: PeriodType | null
  start?: HHMM | null
  end?: HHMM | null
  ticket_number?: string | null
  title?: string | null
  requester_name?: string | null
  requires_video?: boolean | null
  attendees?: number | null
  notes?: string | null
  lock_id?: string | null
}

export interface BookingCancel {
  version?: number | null
  reason?: string | null
}

export interface BookingStatusIn {
  version?: number | null
  status: 'CONFIRMED' | 'CHECKED_IN' | 'NO_SHOW'
}

export interface BookingOut {
  id: string
  room_id: number
  room_name: string
  ticket_number: string | null
  ticket_url: string | null
  title: string | null
  requester_name: string
  notes: string | null
  attendees: number | null
  date: ISODate
  start: HHMM
  end: HHMM
  start_time: ISODateTime
  end_time: ISODateTime
  period_type: PeriodType
  is_full_day: boolean
  requires_video: boolean
  status: BookingStatus
  version: number
  /** Selo "Reunião Presencial — Remanejável": sem vídeo ocupando sala VIDEO operacional. */
  is_relocatable: boolean
  /** Precisa de vídeo mas a sala não tem vídeo operacional. */
  video_unavailable: boolean
  created_by: UserBrief
  updated_by: UserBrief | null
  created_at: ISODateTime
  updated_at: ISODateTime
  cancelled_at: ISODateTime | null
  cancel_reason: string | null
}

export interface BookingWriteResult {
  booking: BookingOut
  warnings: BusinessWarning[]
}

/** Estado gravado em booking_logs (serializers.booking_snapshot). */
export interface BookingSnapshot {
  room_id: number
  room_name: string | null
  ticket_number: string | null
  title: string | null
  requester_name: string
  notes: string | null
  attendees: number | null
  date: ISODate
  start: HHMM
  end: HHMM
  period_type: PeriodType
  is_full_day: boolean
  requires_video: boolean
  status: BookingStatus
  version: number
}

export interface BookingLogOut {
  id: number
  booking_id: string
  user: UserBrief | null
  action: LogAction
  previous_state: Partial<BookingSnapshot> | null
  new_state: Partial<BookingSnapshot> | null
  created_at: ISODateTime
}

export interface AuditEntryOut extends BookingLogOut {
  booking_ticket_number: string | null
  booking_requester_name: string | null
  booking_room_name: string | null
  booking_date: ISODate | null
}

// ------------------------------------------------------------------ disponibilidade

export interface FreeRoomsQuery {
  date: ISODate
  start?: HHMM
  end?: HHMM
  period_type?: PeriodType
  room_type?: RoomType
  exclude_booking_id?: string
}

export interface AuditQuery {
  /** Dia (local) em que a ação aconteceu. */
  date?: ISODate
  /** Dia da reserva afetada (extensão do backend além do contrato §5.4). */
  booking_date?: ISODate
  user_id?: number
  action?: LogAction
  limit?: number
  offset?: number
}

// ------------------------------------------------------------------ soft locks / tempo real

export interface LockOut {
  lock_id: string
  room_id: number
  date: ISODate
  start: HHMM
  end: HHMM
  user: UserBrief
  expires_at: ISODateTime
  /** Preenchido quando a trava é para mover/editar uma reserva existente. */
  booking_id: string | null
}

export interface OnlineUser {
  id: number
  full_name: string
  connections: number
}

// ------------------------------------------------------------------ configuração / saúde

export interface PeriodWindow {
  start: HHMM
  end: HHMM
}

export interface ConfigOut {
  timezone: string
  today: ISODate
  day_start: HHMM
  day_end: HHMM
  slot_minutes: number
  morning: PeriodWindow
  afternoon: PeriodWindow
  /** 0 = segunda-feira (datetime.weekday do Python). */
  work_days: number[]
  lock_ttl_seconds: number
  ticket_url_template: string | null
  realtime_backend: 'redis' | 'memory'
}

export interface HealthOut {
  /** "degraded" quando o Redis configurado está fora (o banco responde; 503 só sem banco). */
  status: 'ok' | 'degraded' | (string & {})
  database: boolean
  realtime: 'redis' | 'memory'
  redis: 'ok' | 'down' | 'disabled'
}

// ------------------------------------------------------------------ importação

export type ImportSheetStatus = 'IMPORTED' | 'DRY_RUN' | 'SKIPPED_PAST' | 'SKIPPED_UNRECOGNIZED' | 'ERROR'

export interface ImportSheetReport {
  sheet_name: string
  date: ISODate | null
  status: ImportSheetStatus
  bookings_found: number
  bookings_imported: number
  bookings_skipped: number
  warnings: string[]
}

export interface ImportReport {
  dry_run: boolean
  sheets: ImportSheetReport[]
  total_found: number
  total_imported: number
  total_skipped: number
  unknown_rooms: string[]
  errors: string[]
}

// ------------------------------------------------------------------ WebSocket

export type ClientMessageType = 'LOCK_SLOT' | 'UNLOCK_SLOT' | 'REFRESH_LOCK' | 'PING'

export type EventType =
  | 'HELLO'
  | 'LOCK_ACQUIRED'
  | 'LOCK_DENIED'
  | 'LOCK_REFRESHED'
  | 'PONG'
  | 'ERROR'
  | 'SLOT_LOCKED'
  | 'SLOT_UNLOCKED'
  | 'BOOKING_CREATED'
  | 'BOOKING_UPDATED'
  | 'BOOKING_CANCELLED'
  | 'BOOKINGS_IMPORTED'
  | 'ROOM_CREATED'
  | 'ROOM_UPDATED'
  | 'PRESENCE'

export interface Envelope<T extends string = string, D = Record<string, unknown>> {
  type: T
  data: D
  ts: ISODateTime
  request_id?: string
}

export interface LockSlotData {
  room_id: number
  date: ISODate
  start: HHMM
  end: HHMM
  booking_id?: string | null
}

export interface LockIdData {
  lock_id: string
}

export interface ClientMessageData {
  LOCK_SLOT: LockSlotData
  UNLOCK_SLOT: LockIdData
  REFRESH_LOCK: LockIdData
  PING: Record<string, never>
}

export type UnlockReason = 'released' | 'saved' | 'expired' | 'disconnected'

export interface BookingPrevious {
  room_id: number
  date: ISODate
  start: HHMM
  end: HHMM
}

export interface ServerEventData {
  HELLO: {
    user: UserBrief
    instance_id: string
    server_time: ISODateTime
    locks: LockOut[]
    online: OnlineUser[]
  }
  LOCK_ACQUIRED: { lock: LockOut }
  LOCK_DENIED: { reason: 'LOCKED_BY_OTHER' | (string & {}); message: string; conflicts: LockOut[] }
  LOCK_REFRESHED: { lock: LockOut }
  PONG: Record<string, never>
  ERROR: { code: string; message: string }
  SLOT_LOCKED: { lock: LockOut }
  SLOT_UNLOCKED: { lock: LockOut; reason: UnlockReason }
  BOOKING_CREATED: { booking: BookingOut }
  BOOKING_UPDATED: { booking: BookingOut; previous: BookingPrevious }
  BOOKING_CANCELLED: { booking: BookingOut }
  BOOKINGS_IMPORTED: { dates: ISODate[]; count: number }
  ROOM_CREATED: { room: RoomOut }
  ROOM_UPDATED: { room: RoomOut }
  PRESENCE: { online: OnlineUser[] }
}

/** União discriminada de todos os envelopes servidor → cliente. */
export type ServerEvent = {
  [K in EventType]: Envelope<K, ServerEventData[K]>
}[EventType]

export type ServerEventOf<K extends EventType> = Envelope<K, ServerEventData[K]>
