/**
 * Caminhos, portas e variáveis de ambiente do backend usado pelos testes E2E.
 * Tudo pode ser trocado por variáveis E2E_* (ex.: rodar em outra porta ou outro banco).
 */
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))

export const E2E_DIR = path.resolve(here, '..')
export const FRONTEND_DIR = path.resolve(E2E_DIR, '..')
export const REPO_DIR = path.resolve(FRONTEND_DIR, '..')
export const BACKEND_DIR = path.join(REPO_DIR, 'backend')
export const DIST_DIR = path.join(FRONTEND_DIR, 'dist')

/** Sessões salvas (storageState) e o manifesto do seed, gerados pelo global-setup. */
export const STATE_DIR = path.join(E2E_DIR, '.auth')
export const SEED_MANIFEST = path.join(STATE_DIR, 'seed.json')
/** Capturas das telas principais (ignoradas pelo git). */
export const SCREENSHOTS_DIR = path.join(E2E_DIR, 'screenshots')

const venvPython =
  process.platform === 'win32'
    ? path.join(BACKEND_DIR, '.venv', 'Scripts', 'python.exe')
    : path.join(BACKEND_DIR, '.venv', 'bin', 'python')
export const PYTHON = process.env.E2E_PYTHON ?? venvPython

export const PORT = Number(process.env.E2E_PORT ?? 8100)
export const BASE_URL = `http://127.0.0.1:${PORT}`

/** Servidor PostgreSQL (sem o nome do banco) e o banco exclusivo dos testes E2E. */
export const PG_SERVER_URL = process.env.E2E_PG_URL ?? 'postgresql://postgres@127.0.0.1:5433'
export const DB_NAME = process.env.E2E_DB_NAME ?? 'agenda_e2e'
export const DATABASE_URL = `${PG_SERVER_URL.replace(/^postgresql:/, 'postgresql+asyncpg:')}/${DB_NAME}`

export const TICKET_URL_TEMPLATE = 'https://chamados.example.com/ticket/{ticket}'

export function ticketUrl(ticket: string): string {
  return TICKET_URL_TEMPLATE.replace('{ticket}', encodeURIComponent(ticket))
}

/** Variáveis do backend (uvicorn e CLI) apontando para o banco E2E e servindo o build do frontend. */
export function backendEnv(admin: { username: string; password: string; fullName: string }): Record<string, string> {
  return {
    AGENDA_ENV: 'development',
    AGENDA_DATABASE_URL: DATABASE_URL,
    AGENDA_REDIS_URL: '',
    AGENDA_FRONTEND_DIST: DIST_DIR,
    AGENDA_TICKET_URL_TEMPLATE: TICKET_URL_TEMPLATE,
    AGENDA_BOOTSTRAP_ADMIN_USERNAME: admin.username,
    AGENDA_BOOTSTRAP_ADMIN_PASSWORD: admin.password,
    AGENDA_BOOTSTRAP_ADMIN_NAME: admin.fullName,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
  }
}
