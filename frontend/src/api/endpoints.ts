import { downloadFile, http } from './client'
import type {
  AuditEntryOut,
  AuditQuery,
  BookingCancel,
  BookingCreate,
  BookingLogOut,
  BookingOut,
  BookingStatusIn,
  BookingUpdate,
  BookingWriteResult,
  ConfigOut,
  FreeRoomsQuery,
  HealthOut,
  ImportReport,
  ISODate,
  LoginIn,
  RoomCreate,
  RoomOut,
  RoomUpdate,
  TokenOut,
  UserCreate,
  UserOut,
  UserUpdate,
} from './types'

const enc = encodeURIComponent

/** Todas as rotas REST do contrato (§5), tipadas. */
export const api = {
  auth: {
    login: (body: LoginIn) => http.post<TokenOut>('/api/auth/login', body, { auth: false }),
    me: () => http.get<UserOut>('/api/auth/me'),
  },

  config: {
    get: () => http.get<ConfigOut>('/api/config'),
    health: () => http.get<HealthOut>('/api/health', { auth: false }),
  },

  users: {
    list: () => http.get<UserOut[]>('/api/users'),
    create: (body: UserCreate) => http.post<UserOut>('/api/users', body),
    update: (id: number, body: UserUpdate) => http.patch<UserOut>(`/api/users/${id}`, body),
  },

  rooms: {
    list: (includeInactive = false) =>
      http.get<RoomOut[]>('/api/rooms', { query: { include_inactive: includeInactive } }),
    create: (body: RoomCreate) => http.post<RoomOut>('/api/rooms', body),
    update: (id: number, body: RoomUpdate) => http.patch<RoomOut>(`/api/rooms/${id}`, body),
    setMaintenance: (id: number, ticketId: string) =>
      http.put<RoomOut>(`/api/rooms/${id}/maintenance`, { ticket_id: ticketId }),
    clearMaintenance: (id: number) => http.delete<RoomOut>(`/api/rooms/${id}/maintenance`),
  },

  bookings: {
    listDay: (date: ISODate, includeCancelled = false) =>
      http.get<BookingOut[]>('/api/bookings', { query: { date, include_cancelled: includeCancelled } }),
    listRange: (startDate: ISODate, endDate: ISODate, includeCancelled = false) =>
      http.get<BookingOut[]>('/api/bookings', {
        query: { start_date: startDate, end_date: endDate, include_cancelled: includeCancelled },
      }),
    search: (q: string, limit = 20, signal?: AbortSignal) =>
      http.get<BookingOut[]>('/api/bookings/search', { query: { q, limit }, signal }),
    get: (id: string) => http.get<BookingOut>(`/api/bookings/${enc(id)}`),
    create: (body: BookingCreate) => http.post<BookingWriteResult>('/api/bookings', body),
    update: (id: string, body: BookingUpdate) => http.patch<BookingWriteResult>(`/api/bookings/${enc(id)}`, body),
    cancel: (id: string, body: BookingCancel) => http.post<BookingOut>(`/api/bookings/${enc(id)}/cancel`, body),
    setStatus: (id: string, body: BookingStatusIn) => http.post<BookingOut>(`/api/bookings/${enc(id)}/status`, body),
    logs: (id: string) => http.get<BookingLogOut[]>(`/api/bookings/${enc(id)}/logs`),
  },

  audit: {
    list: (query: AuditQuery) => http.get<AuditEntryOut[]>('/api/audit', { query: { ...query } }),
  },

  availability: {
    freeRooms: (query: FreeRoomsQuery, signal?: AbortSignal) =>
      http.get<RoomOut[]>('/api/availability/free-rooms', { query: { ...query }, signal }),
    fullDay: (date: ISODate) => http.get<RoomOut[]>('/api/availability/full-day', { query: { date } }),
  },

  excel: {
    exportDay: (date: ISODate) => downloadFile('/api/export/day.xlsx', { date }, `agenda-${date}.xlsx`),
    exportWeek: (startDate: ISODate) =>
      downloadFile('/api/export/week.xlsx', { start_date: startDate }, `agenda-semana-${startDate}.xlsx`),
    importXlsx: (file: File, { dryRun, includePast }: { dryRun: boolean; includePast: boolean }) => {
      const form = new FormData()
      form.append('file', file)
      return http.post<ImportReport>('/api/import/xlsx', undefined, {
        query: { dry_run: dryRun, include_past: includePast },
        form,
      })
    },
  },
}

export type Api = typeof api
