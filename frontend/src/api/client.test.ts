import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { buildUrl, configureClient, fetchFile, filenameFromContentDisposition, request } from './client'
import {
  ApiError,
  asConflict,
  asRoomHasFutureBookings,
  asSlotLocked,
  asStaleVersion,
  errorFromBody,
  retryAfterSeconds,
  translateValidationItem,
} from './errors'

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

const fetchMock = vi.fn<typeof fetch>()
const onUnauthorized = vi.fn()

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
  onUnauthorized.mockReset()
  configureClient({ getToken: () => 'tok-123', onUnauthorized })
})

afterEach(() => {
  configureClient({ getToken: () => null, onUnauthorized: () => {} })
})

describe('buildUrl', () => {
  it('omite parâmetros vazios', () => {
    expect(buildUrl('/api/audit', { date: '2026-10-26', user_id: undefined, action: '', limit: 50 })).toBe(
      '/api/audit?date=2026-10-26&limit=50',
    )
    expect(buildUrl('/api/rooms', { include_inactive: false })).toBe('/api/rooms?include_inactive=false')
    expect(buildUrl('/api/x')).toBe('/api/x')
  })
})

describe('request', () => {
  it('envia o Bearer e o corpo JSON', async () => {
    fetchMock.mockResolvedValue(jsonResponse(201, { ok: true }))
    await expect(request('POST', '/api/bookings', { body: { a: 1 } })).resolves.toEqual({ ok: true })
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/bookings')
    expect(init?.method).toBe('POST')
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer tok-123')
    expect((init?.headers as Record<string, string>)['Content-Type']).toBe('application/json')
    expect(init?.body).toBe('{"a":1}')
  })

  it('não envia Bearer em rotas públicas (login) e não trata 401 como sessão expirada', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(401, { detail: { code: 'INVALID_CREDENTIALS', message: 'Usuário ou senha incorretos.' } }),
    )
    const error = await request('POST', '/api/auth/login', { body: {}, auth: false }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).code).toBe('INVALID_CREDENTIALS')
    expect((fetchMock.mock.calls[0]![1]?.headers as Record<string, string>).Authorization).toBeUndefined()
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('401 numa rota autenticada → logout', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(401, { detail: { code: 'UNAUTHORIZED', message: 'Sessão expirada ou inválida. Entre novamente.' } }),
    )
    await expect(request('GET', '/api/auth/me')).rejects.toMatchObject({ status: 401, code: 'UNAUTHORIZED' })
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('D2: o motivo do 401 (SESSION_REVOKED / ACCOUNT_DISABLED) chega a quem encerra a sessão', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(401, { detail: { code: 'SESSION_REVOKED', message: 'A senha foi alterada.' } }),
    )
    await request('GET', '/api/bookings').catch(() => {})
    expect(onUnauthorized).toHaveBeenLastCalledWith(expect.objectContaining({ code: 'SESSION_REVOKED' }))

    fetchMock.mockResolvedValueOnce(
      jsonResponse(403, { detail: { code: 'ACCOUNT_DISABLED', message: 'Acesso desativado.' } }),
    )
    await request('GET', '/api/bookings').catch(() => {})
    expect(onUnauthorized).toHaveBeenLastCalledWith(expect.objectContaining({ code: 'ACCOUNT_DISABLED' }))

    // 403 comum (sem permissão) não derruba a sessão
    fetchMock.mockResolvedValueOnce(jsonResponse(403, { detail: { code: 'FORBIDDEN', message: 'Sem permissão.' } }))
    await request('GET', '/api/users').catch(() => {})
    expect(onUnauthorized).toHaveBeenCalledTimes(2)
  })

  it('R14: 401 de uma requisição feita com um token que já foi trocado (outra aba) não derruba a sessão nova', async () => {
    let token = 'antigo'
    configureClient({ getToken: () => token, onUnauthorized })
    fetchMock.mockImplementation(async () => {
      token = 'novo' // outra aba entrou de novo enquanto a resposta não chegava
      return jsonResponse(401, { detail: { code: 'UNAUTHORIZED', message: 'Expirada.' } })
    })
    await request('GET', '/api/bookings').catch(() => {})
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('D4: 429 do login traz retry_after_seconds (do corpo ou do header Retry-After)', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        429,
        { detail: { code: 'TOO_MANY_ATTEMPTS', message: 'Muitas tentativas.', retry_after_seconds: 60 } },
        { 'Retry-After': '60' },
      ),
    )
    let error = await request('POST', '/api/auth/login', { body: {}, auth: false }).catch((e: unknown) => e)
    expect(retryAfterSeconds(error)).toBe(60)

    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        429,
        { detail: { code: 'TOO_MANY_ATTEMPTS', message: 'Muitas tentativas.' } },
        { 'Retry-After': '300' },
      ),
    )
    error = await request('POST', '/api/auth/login', { body: {}, auth: false }).catch((e: unknown) => e)
    expect(error).toMatchObject({ status: 429, code: 'TOO_MANY_ATTEMPTS' })
    expect(retryAfterSeconds(error)).toBe(300)
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('erro de negócio vira ApiError com extra tipado', async () => {
    const conflicts = [{ id: 'b1' }]
    const alternatives = [{ id: 5, name: 'Sala 05' }]
    fetchMock.mockResolvedValue(
      jsonResponse(409, { detail: { code: 'BOOKING_CONFLICT', message: 'Horário ocupado.', conflicts, alternatives } }),
    )
    const error = await request('POST', '/api/bookings', { body: {} }).catch((e: unknown) => e)
    expect(error).toMatchObject({ status: 409, code: 'BOOKING_CONFLICT', message: 'Horário ocupado.' })
    expect((error as ApiError).extra).toEqual({ conflicts, alternatives })
    expect(asConflict(error)).toEqual({
      code: 'BOOKING_CONFLICT',
      message: 'Horário ocupado.',
      conflicts,
      alternatives,
    })
    expect(asSlotLocked(error)).toBeNull()
  })

  it('falha de rede vira NETWORK_ERROR em pt-BR', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(request('GET', '/api/config')).rejects.toMatchObject({ status: 0, code: 'NETWORK_ERROR' })
  })

  it('cancelamento (AbortController) continua sendo AbortError', async () => {
    fetchMock.mockRejectedValue(new DOMException('aborted', 'AbortError'))
    await expect(request('GET', '/api/bookings/search')).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('204 devolve undefined', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await expect(request('DELETE', '/api/x')).resolves.toBeUndefined()
  })
})

describe('errorFromBody', () => {
  it('traduz erros de validação do FastAPI (422) por campo', () => {
    const error = errorFromBody(422, {
      detail: [
        {
          loc: ['body', 'requester_name'],
          msg: 'String should have at least 1 character',
          type: 'string_too_short',
          ctx: { min_length: 1 },
        },
      ],
    })
    expect(error.code).toBe('VALIDATION_ERROR')
    expect(error.message).toBe('Solicitante: não pode ficar em branco')
    expect(error.fieldErrors).toEqual([{ field: 'requester_name', message: 'Solicitante: não pode ficar em branco' }])
  })

  it('junta vários erros de validação', () => {
    const error = errorFromBody(422, {
      detail: [
        { loc: ['body', 'room_id'], msg: 'Field required', type: 'missing' },
        { loc: ['body', 'attendees'], msg: '', type: 'greater_than', ctx: { gt: 0 } },
      ],
    })
    expect(error.message).toBe('Verifique os campos: Sala: campo obrigatório; Participantes: deve ser maior que 0')
  })

  it('usa mensagens genéricas pt-BR quando não há código', () => {
    expect(errorFromBody(500, null)).toMatchObject({ code: 'SERVER_ERROR' })
    expect(errorFromBody(500, null).message).toContain('Erro inesperado no servidor (500)')
    expect(errorFromBody(404, { detail: 'Not Found' })).toMatchObject({
      code: 'NOT_FOUND',
      message: 'Recurso não encontrado.',
    })
    expect(errorFromBody(403, {})).toMatchObject({ code: 'FORBIDDEN' })
  })

  it('extrai STALE_VERSION com a versão atual', () => {
    const current = { id: 'b1', version: 7 }
    const error = errorFromBody(409, { detail: { code: 'STALE_VERSION', message: 'Alterada.', current } })
    expect(asStaleVersion(error)?.current).toEqual(current)
    expect(asStaleVersion(errorFromBody(409, { detail: { code: 'STALE_VERSION', message: 'x' } }))).toBeNull()
  })

  it('D1: extrai ROOM_HAS_FUTURE_BOOKINGS com a contagem e a lista', () => {
    const bookings = [{ id: 'b1', date: '2026-11-03' }]
    const error = errorFromBody(409, {
      detail: { code: 'ROOM_HAS_FUTURE_BOOKINGS', message: 'A sala tem 3 reservas futuras.', count: 3, bookings },
    })
    expect(asRoomHasFutureBookings(error)).toEqual({
      code: 'ROOM_HAS_FUTURE_BOOKINGS',
      message: 'A sala tem 3 reservas futuras.',
      count: 3,
      bookings,
    })
    expect(
      asRoomHasFutureBookings(errorFromBody(409, { detail: { code: 'ROOM_NAME_TAKEN', message: 'x' } })),
    ).toBeNull()
  })

  it('extrai SLOT_LOCKED com as travas', () => {
    const locks = [{ lock_id: 'l1' }]
    expect(
      asSlotLocked(errorFromBody(409, { detail: { code: 'SLOT_LOCKED', message: 'Fulana…', locks } }))?.locks,
    ).toEqual(locks)
  })

  it('traduz tipos comuns do Pydantic', () => {
    expect(translateValidationItem({ loc: [], msg: '', type: 'string_too_long', ctx: { max_length: 50 } })).toBe(
      'deve ter no máximo 50 caracteres',
    )
    expect(translateValidationItem({ loc: [], msg: '', type: 'string_pattern_mismatch' })).toBe(
      'contém caracteres não permitidos',
    )
    expect(translateValidationItem({ loc: [], msg: '', type: 'int_parsing' })).toBe('deve ser um número inteiro')
    expect(translateValidationItem({ loc: [], msg: '', type: 'algo_novo' })).toBe('valor inválido')
  })
})

describe('downloads (xlsx)', () => {
  it('lê o nome do arquivo do Content-Disposition', () => {
    expect(filenameFromContentDisposition('attachment; filename="agenda-2026-10-26.xlsx"')).toBe(
      'agenda-2026-10-26.xlsx',
    )
    expect(filenameFromContentDisposition('attachment; filename=agenda.xlsx')).toBe('agenda.xlsx')
    expect(
      filenameFromContentDisposition(`attachment; filename="agenda.xlsx"; filename*=UTF-8''agenda-semana-26%2F10.xlsx`),
    ).toBe('agenda-semana-26/10.xlsx')
    expect(filenameFromContentDisposition(null)).toBeNull()
    expect(filenameFromContentDisposition('inline')).toBeNull()
  })

  it('baixa com Bearer e usa o nome do servidor (ou o padrão)', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(new Blob(['xlsx']), {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="agenda-2026-10-26.xlsx"' },
      }),
    )
    const file = await fetchFile('/api/export/day.xlsx', { date: '2026-10-26' }, 'padrao.xlsx')
    expect(file.filename).toBe('agenda-2026-10-26.xlsx')
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/export/day.xlsx?date=2026-10-26')
    expect((fetchMock.mock.calls[0]![1]?.headers as Record<string, string>).Authorization).toBe('Bearer tok-123')

    fetchMock.mockResolvedValueOnce(new Response(new Blob(['xlsx']), { status: 200 }))
    expect((await fetchFile('/api/export/week.xlsx', undefined, 'padrao.xlsx')).filename).toBe('padrao.xlsx')
  })
})
