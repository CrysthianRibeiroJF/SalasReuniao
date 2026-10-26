/**
 * Cliente REST mínimo para preparar e conferir dados nos testes E2E (seed, arranjo, asserções).
 * A interface em si é sempre exercitada pelo navegador; isto só evita cliques que não são o
 * objeto do teste.
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'

import { type APIRequestContext, type APIResponse, request } from '@playwright/test'

import type {
  BookingCreate,
  BookingLogOut,
  BookingOut,
  BookingUpdate,
  BookingWriteResult,
  ConfigOut,
  ImportReport,
  ISODate,
  RoomCreate,
  RoomOut,
  RoomUpdate,
  TokenOut,
  UserCreate,
  UserOut,
  UserUpdate,
} from '../../src/api/types'
import { BASE_URL } from './env'

export class ApiFailure extends Error {
  constructor(
    readonly status: number,
    readonly body: unknown,
    what: string,
  ) {
    super(`${what}: HTTP ${status} ${JSON.stringify(body)}`)
  }
}

async function json<T>(response: APIResponse, what: string): Promise<T> {
  const text = await response.text()
  const body: unknown = text ? JSON.parse(text) : null
  if (!response.ok()) throw new ApiFailure(response.status(), body, what)
  return body as T
}

export class AgendaApi {
  private constructor(
    private readonly http: APIRequestContext,
    readonly session: TokenOut,
  ) {}

  /** Entra com usuário e senha (POST /api/auth/login) e devolve um cliente autenticado. */
  static async login(username: string, password: string): Promise<AgendaApi> {
    const anonymous = await request.newContext({ baseURL: BASE_URL })
    try {
      const session = await json<TokenOut>(
        await anonymous.post('/api/auth/login', { data: { username, password } }),
        `login de ${username}`,
      )
      const http = await request.newContext({
        baseURL: BASE_URL,
        extraHTTPHeaders: { Authorization: `Bearer ${session.access_token}` },
      })
      return new AgendaApi(http, session)
    } finally {
      await anonymous.dispose()
    }
  }

  async dispose(): Promise<void> {
    await this.http.dispose()
  }

  config(): Promise<ConfigOut> {
    return this.get('/api/config')
  }

  /** "Hoje" no fuso da recepção, segundo o servidor (pode virar durante a execução da suíte). */
  async today(): Promise<ISODate> {
    return (await this.config()).today
  }

  rooms(includeInactive = false): Promise<RoomOut[]> {
    return this.get(`/api/rooms?include_inactive=${includeInactive}`)
  }

  async createRoom(body: RoomCreate): Promise<RoomOut> {
    return json(await this.http.post('/api/rooms', { data: body }), `criar sala ${body.name}`)
  }

  async clearMaintenance(roomId: number): Promise<RoomOut> {
    return json(await this.http.delete(`/api/rooms/${roomId}/maintenance`), 'encerrar manutenção')
  }

  users(): Promise<UserOut[]> {
    return this.get('/api/users')
  }

  async createUser(body: UserCreate): Promise<UserOut> {
    return json(await this.http.post('/api/users', { data: body }), `criar usuário ${body.username}`)
  }

  async updateUser(id: number, body: UserUpdate): Promise<UserOut> {
    return json(await this.http.patch(`/api/users/${id}`, { data: body }), `editar usuário ${id}`)
  }

  async updateRoom(id: number, body: RoomUpdate): Promise<RoomOut> {
    return json(await this.http.patch(`/api/rooms/${id}`, { data: body }), `editar sala ${id}`)
  }

  bookings(date: ISODate, includeCancelled = false): Promise<BookingOut[]> {
    return this.get(`/api/bookings?date=${date}&include_cancelled=${includeCancelled}`)
  }

  booking(id: string): Promise<BookingOut> {
    return this.get(`/api/bookings/${id}`)
  }

  logs(id: string): Promise<BookingLogOut[]> {
    return this.get(`/api/bookings/${id}/logs`)
  }

  async createBooking(body: BookingCreate): Promise<BookingOut> {
    const result = await json<BookingWriteResult>(
      await this.http.post('/api/bookings', { data: body }),
      `criar reserva de ${body.requester_name}`,
    )
    return result.booking
  }

  async updateBooking(id: string, body: BookingUpdate): Promise<BookingOut> {
    const result = await json<BookingWriteResult>(
      await this.http.patch(`/api/bookings/${id}`, { data: body }),
      'editar reserva',
    )
    return result.booking
  }

  async cancelBooking(id: string, reason?: string): Promise<BookingOut> {
    return json(await this.http.post(`/api/bookings/${id}/cancel`, { data: { reason } }), 'cancelar reserva')
  }

  /** POST /api/import/xlsx (admin) com o arquivo local; `dryRun` false grava de verdade. */
  async importXlsx(file: string, { dryRun = true, includePast = false } = {}): Promise<ImportReport> {
    const response = await this.http.post(`/api/import/xlsx?dry_run=${dryRun}&include_past=${includePast}`, {
      multipart: {
        file: {
          name: path.basename(file),
          mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          buffer: readFileSync(file),
        },
      },
    })
    return json(response, `importar ${path.basename(file)}`)
  }

  /**
   * Cancela as reservas ativas do dia (ou só as de uma sala), para o teste começar de uma grade
   * conhecida e poder ser repetido sem recriar o banco.
   */
  async clearDay(date: ISODate, { roomId }: { roomId?: number } = {}): Promise<void> {
    for (const booking of await this.bookings(date)) {
      if (roomId === undefined || booking.room_id === roomId) {
        await this.cancelBooking(booking.id, 'limpeza do teste E2E')
      }
    }
  }

  private async get<T>(path: string): Promise<T> {
    return json(await this.http.get(path), `GET ${path}`)
  }
}
