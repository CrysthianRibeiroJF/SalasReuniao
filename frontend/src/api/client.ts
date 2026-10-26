import { ApiError, errorFromBody, networkError } from './errors'

export type QueryValue = string | number | boolean | null | undefined
export type Query = Record<string, QueryValue>

export interface RequestOptions {
  query?: Query
  /** Corpo JSON. */
  body?: unknown
  /** Corpo multipart (upload). Tem precedência sobre `body`. */
  form?: FormData
  /** false = não envia o Bearer nem trata 401 como sessão expirada (ex.: login). */
  auth?: boolean
  signal?: AbortSignal
}

interface ClientHooks {
  getToken: () => string | null
  /** O servidor recusou a sessão (401); `error.code` diz o motivo (UNAUTHORIZED, SESSION_REVOKED, ACCOUNT_DISABLED). */
  onUnauthorized: (error: ApiError) => void
}

const hooks: ClientHooks = {
  getToken: () => null,
  onUnauthorized: () => {},
}

/** Liga o cliente HTTP ao estado de autenticação (feito uma vez no bootstrap da aplicação). */
export function configureClient(next: Partial<ClientHooks>): void {
  Object.assign(hooks, next)
}

export function buildUrl(path: string, query?: Query): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    params.append(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${path}${path.includes('?') ? '&' : '?'}${qs}` : path
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

async function send(method: string, path: string, options: RequestOptions, accept: string): Promise<Response> {
  const { query, body, form, auth = true, signal } = options
  const headers: Record<string, string> = { Accept: accept }
  let payload: BodyInit | undefined
  if (form) {
    payload = form
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  const token = auth ? hooks.getToken() : null
  if (token) headers.Authorization = `Bearer ${token}`

  let response: Response
  try {
    response = await fetch(buildUrl(path, query), { method, headers, body: payload, signal })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw networkError()
  }

  if (!response.ok) {
    let parsed = errorFromBody(response.status, await readJson(response))
    if (response.status === 429 && parsed.extra.retry_after_seconds === undefined) {
      const retryAfter = Number(response.headers.get('Retry-After'))
      if (Number.isFinite(retryAfter) && retryAfter > 0) {
        parsed = new ApiError(parsed.status, parsed.code, parsed.message, {
          ...parsed.extra,
          retry_after_seconds: retryAfter,
        })
      }
    }
    // Só encerra a sessão se a resposta for do token atual (outra aba pode ter entrado de novo no meio).
    if (auth && token && token === hooks.getToken() && endsSession(response.status, parsed)) {
      hooks.onUnauthorized(parsed)
    }
    throw parsed
  }
  return response
}

/** 401 (token vencido, revogado ou conta desativada) encerra a sessão; 403 comum (sem permissão) não. */
function endsSession(status: number, error: ApiError): boolean {
  return status === 401 || (status === 403 && error.code === 'ACCOUNT_DISABLED')
}

/** Requisição JSON tipada. Erros viram ApiError (status, code, message, extra). */
export async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(method, path, options, 'application/json')
  if (response.status === 204) return undefined as T
  return (await readJson(response)) as T
}

export const http = {
  get: <T>(path: string, options?: RequestOptions) => request<T>('GET', path, options),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) => request<T>('POST', path, { ...options, body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) => request<T>('PATCH', path, { ...options, body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) => request<T>('PUT', path, { ...options, body }),
  delete: <T>(path: string, options?: RequestOptions) => request<T>('DELETE', path, options),
}

/**
 * Extrai o nome do arquivo de um header Content-Disposition.
 * Suporta `filename*=UTF-8''...` (RFC 5987, preferido) e `filename="..."`.
 */
export function filenameFromContentDisposition(header: string | null): string | null {
  if (!header) return null
  const extended = /filename\*\s*=\s*([^']*)'[^']*'([^;]+)/i.exec(header)
  if (extended?.[2]) {
    try {
      return decodeURIComponent(extended[2].trim().replace(/^"|"$/g, ''))
    } catch {
      // cai para o filename simples
    }
  }
  const simple = /filename\s*=\s*("([^"]*)"|[^;]+)/i.exec(header)
  const value = simple?.[2] ?? simple?.[1]
  return value ? value.trim().replace(/^"|"$/g, '') : null
}

export interface DownloadedFile {
  blob: Blob
  filename: string
}

/** Baixa um arquivo autenticado (fetch → Blob) e devolve o nome vindo do Content-Disposition. */
export async function fetchFile(path: string, query: Query | undefined, fallbackName: string): Promise<DownloadedFile> {
  const response = await send('GET', path, { query }, '*/*')
  const filename = filenameFromContentDisposition(response.headers.get('Content-Disposition')) ?? fallbackName
  return { blob: await response.blob(), filename }
}

/** Entrega o Blob ao navegador como download, via link temporário. */
export function saveBlob({ blob, filename }: DownloadedFile): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.rel = 'noopener'
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1_000)
}

export async function downloadFile(path: string, query: Query | undefined, fallbackName: string): Promise<string> {
  const file = await fetchFile(path, query, fallbackName)
  saveBlob(file)
  return file.filename
}

export { ApiError }
