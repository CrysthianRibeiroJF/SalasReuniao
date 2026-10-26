import type {
  BookingConflictDetail,
  ErrorCode,
  RoomHasFutureBookingsDetail,
  SlotLockedDetail,
  StaleVersionDetail,
  ValidationErrorItem,
} from './types'

export interface FieldError {
  /** Nome do campo no corpo da requisição (ex.: "requester_name"), quando identificável. */
  field: string | null
  message: string
}

/** Erro tipado de qualquer chamada à API (HTTP, validação ou rede). */
export class ApiError extends Error {
  readonly status: number
  readonly code: ErrorCode | (string & {})
  /** Demais campos de `detail` além de code/message (ex.: conflicts, alternatives, locks, current). */
  readonly extra: Record<string, unknown>
  readonly fieldErrors: FieldError[]

  constructor(
    status: number,
    code: string,
    message: string,
    extra: Record<string, unknown> = {},
    fieldErrors: FieldError[] = [],
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.extra = extra
    this.fieldErrors = fieldErrors
  }

  is(code: ErrorCode): boolean {
    return this.code === code
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

export function asConflict(error: unknown): BookingConflictDetail | null {
  if (!isApiError(error) || error.code !== 'BOOKING_CONFLICT') return null
  return {
    code: 'BOOKING_CONFLICT',
    message: error.message,
    conflicts: Array.isArray(error.extra.conflicts)
      ? (error.extra.conflicts as BookingConflictDetail['conflicts'])
      : [],
    alternatives: Array.isArray(error.extra.alternatives)
      ? (error.extra.alternatives as BookingConflictDetail['alternatives'])
      : [],
  }
}

export function asSlotLocked(error: unknown): SlotLockedDetail | null {
  if (!isApiError(error) || error.code !== 'SLOT_LOCKED') return null
  return {
    code: 'SLOT_LOCKED',
    message: error.message,
    locks: Array.isArray(error.extra.locks) ? (error.extra.locks as SlotLockedDetail['locks']) : [],
  }
}

export function asStaleVersion(error: unknown): StaleVersionDetail | null {
  if (!isApiError(error) || error.code !== 'STALE_VERSION') return null
  const current = error.extra.current
  if (!current || typeof current !== 'object') return null
  return { code: 'STALE_VERSION', message: error.message, current: current as StaleVersionDetail['current'] }
}

export function asRoomHasFutureBookings(error: unknown): RoomHasFutureBookingsDetail | null {
  if (!isApiError(error) || error.code !== 'ROOM_HAS_FUTURE_BOOKINGS') return null
  const bookings = Array.isArray(error.extra.bookings)
    ? (error.extra.bookings as RoomHasFutureBookingsDetail['bookings'])
    : []
  const count = typeof error.extra.count === 'number' ? error.extra.count : bookings.length
  return { code: 'ROOM_HAS_FUTURE_BOOKINGS', message: error.message, count, bookings }
}

/** Segundos até poder tentar de novo (429 TOO_MANY_ATTEMPTS: corpo ou header Retry-After); null se não for o caso. */
export function retryAfterSeconds(error: unknown): number | null {
  if (!isApiError(error) || error.status !== 429) return null
  const value = Number(error.extra.retry_after_seconds)
  return Number.isFinite(value) && value > 0 ? Math.ceil(value) : null
}

/** Mensagem pt-BR para exibir ao usuário a partir de qualquer erro. */
export function errorMessage(error: unknown, fallback = 'Não foi possível concluir a operação.'): string {
  return isApiError(error) && error.message ? error.message : fallback
}

// ------------------------------------------------------------------ parsing de respostas

const FIELD_LABELS: Record<string, string> = {
  username: 'Usuário',
  password: 'Senha',
  full_name: 'Nome completo',
  role: 'Papel',
  is_active: 'Ativo',
  name: 'Nome',
  room_type: 'Tipo de sala',
  capacity: 'Capacidade',
  resources: 'Recursos',
  display_order: 'Ordem de exibição',
  ticket_id: 'Nº do chamado de TI',
  room_id: 'Sala',
  date: 'Data',
  start_date: 'Data inicial',
  end_date: 'Data final',
  period_type: 'Tipo de período',
  start: 'Início',
  end: 'Fim',
  ticket_number: 'Nº do Chamado',
  title: 'Assunto/Área',
  requester_name: 'Solicitante',
  requires_video: 'Precisa de Videoconferência',
  attendees: 'Participantes',
  notes: 'Observações',
  version: 'Versão',
  reason: 'Motivo',
  status: 'Status',
  q: 'Busca',
  file: 'Arquivo',
}

function ctxValue(item: ValidationErrorItem, key: string): string {
  const value = item.ctx?.[key]
  return value === undefined || value === null ? '' : String(value)
}

function plural(n: string, singular: string, pluralForm: string): string {
  return n === '1' ? `${n} ${singular}` : `${n} ${pluralForm}`
}

/** Traduz um item de erro de validação do Pydantic/FastAPI para pt-BR. */
export function translateValidationItem(item: ValidationErrorItem): string {
  switch (item.type) {
    case 'missing':
      return 'campo obrigatório'
    case 'string_too_short':
      return ctxValue(item, 'min_length') === '1'
        ? 'não pode ficar em branco'
        : `deve ter pelo menos ${plural(ctxValue(item, 'min_length'), 'caractere', 'caracteres')}`
    case 'string_too_long':
      return `deve ter no máximo ${plural(ctxValue(item, 'max_length'), 'caractere', 'caracteres')}`
    case 'string_pattern_mismatch':
      return 'contém caracteres não permitidos'
    case 'greater_than':
      return `deve ser maior que ${ctxValue(item, 'gt')}`
    case 'greater_than_equal':
      return `deve ser maior ou igual a ${ctxValue(item, 'ge')}`
    case 'less_than':
      return `deve ser menor que ${ctxValue(item, 'lt')}`
    case 'less_than_equal':
      return `deve ser menor ou igual a ${ctxValue(item, 'le')}`
    case 'int_parsing':
    case 'int_type':
    case 'int_from_float':
      return 'deve ser um número inteiro'
    case 'bool_parsing':
    case 'bool_type':
      return 'valor inválido'
    case 'date_parsing':
    case 'date_from_datetime_parsing':
    case 'date_type':
      return 'data inválida'
    case 'time_parsing':
    case 'time_type':
      return 'horário inválido'
    case 'enum':
    case 'literal_error':
      return 'opção inválida'
    case 'list_type':
      return 'deve ser uma lista'
    case 'string_type':
      return 'deve ser um texto'
    default:
      return 'valor inválido'
  }
}

function fieldFromLoc(loc: (string | number)[]): string | null {
  // loc típico: ["body", "requester_name"] ou ["query", "date"]
  const parts = loc.filter((p) => p !== 'body' && p !== 'query' && p !== 'path')
  const last = [...parts].reverse().find((p): p is string => typeof p === 'string')
  return last ?? null
}

export function validationFieldErrors(items: ValidationErrorItem[]): FieldError[] {
  return items.map((item) => {
    const field = fieldFromLoc(item.loc ?? [])
    const label = field ? (FIELD_LABELS[field] ?? field) : null
    const text = translateValidationItem(item)
    return { field, message: label ? `${label}: ${text}` : capitalize(text) }
  })
}

function capitalize(text: string): string {
  return text ? text[0]!.toUpperCase() + text.slice(1) : text
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function genericMessage(status: number): { code: ErrorCode; message: string } {
  if (status === 401) return { code: 'UNAUTHORIZED', message: 'Sessão expirada ou inválida. Entre novamente.' }
  if (status === 403) return { code: 'FORBIDDEN', message: 'Você não tem permissão para fazer isso.' }
  if (status === 404) return { code: 'NOT_FOUND', message: 'Recurso não encontrado.' }
  if (status === 413) return { code: 'HTTP_ERROR', message: 'O arquivo enviado é grande demais.' }
  if (status === 429)
    return { code: 'TOO_MANY_ATTEMPTS', message: 'Muitas tentativas seguidas. Aguarde um pouco e tente de novo.' }
  if (status >= 500)
    return { code: 'SERVER_ERROR', message: `Erro inesperado no servidor (${status}). Tente novamente em instantes.` }
  return { code: 'HTTP_ERROR', message: `A requisição falhou (${status}).` }
}

/** Constrói um ApiError a partir do status HTTP e do corpo JSON (já decodificado, se houver). */
export function errorFromBody(status: number, body: unknown): ApiError {
  const detail = isRecord(body) ? body.detail : undefined

  if (isRecord(detail) && typeof detail.code === 'string') {
    const { code, message, ...extra } = detail
    const text = typeof message === 'string' && message ? message : genericMessage(status).message
    return new ApiError(status, code, text, extra)
  }

  if (Array.isArray(detail)) {
    const fieldErrors = validationFieldErrors(detail as ValidationErrorItem[])
    const message =
      fieldErrors.length === 1
        ? fieldErrors[0]!.message
        : `Verifique os campos: ${fieldErrors.map((e) => e.message).join('; ')}`
    return new ApiError(status, 'VALIDATION_ERROR', message || 'Dados inválidos.', {}, fieldErrors)
  }

  const generic = genericMessage(status)
  return new ApiError(status, generic.code, generic.message)
}

export function networkError(): ApiError {
  return new ApiError(0, 'NETWORK_ERROR', 'Sem conexão com o servidor. Verifique sua rede e tente novamente.')
}
