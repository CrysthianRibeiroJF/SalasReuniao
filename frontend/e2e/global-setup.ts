/**
 * Preparação única antes dos testes (o backend já foi iniciado pelo `webServer` do Playwright):
 * 1. apaga e recria o banco agenda_e2e e aplica as migrations (`python -m app.cli init-db`, que
 *    também cria o administrador inicial);
 * 2. cadastra salas, usuários e as reservas de leitura pela API REST;
 * 3. grava as sessões (storageState) de cada usuário e o manifesto com os ids criados.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import type { FullConfig } from '@playwright/test'

import { AgendaApi } from './support/api'
import { ROOMS, type RoomKey, SEED_BOOKINGS, USERS, type UserKey, bookingPayload } from './support/data'
import {
  BACKEND_DIR,
  BASE_URL,
  DB_NAME,
  PG_SERVER_URL,
  PYTHON,
  SEED_MANIFEST,
  STATE_DIR,
  backendEnv,
} from './support/env'
import type { SeedManifest } from './support/seed'

/** Chave do localStorage onde a SPA guarda a sessão (src/store/auth.ts). */
const AUTH_STORAGE_KEY = 'agenda-salas:auth'

const RECREATE_DB = `
import asyncio, sys
import asyncpg

async def main(server, name):
    conn = await asyncpg.connect(server + "/postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()

asyncio.run(main(sys.argv[1], sys.argv[2]))
`

function python(args: string[], what: string): void {
  try {
    execFileSync(PYTHON, args, {
      cwd: BACKEND_DIR,
      env: { ...process.env, ...backendEnv(USERS.admin) },
      stdio: ['ignore', 'pipe', 'pipe'],
      encoding: 'utf-8',
    })
  } catch (error) {
    const err = error as { stdout?: string; stderr?: string; message: string }
    throw new Error(`${what} falhou: ${err.stderr || err.stdout || err.message}`, { cause: error })
  }
}

async function waitForHealth(timeoutMs = 30_000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  let last = ''
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${BASE_URL}/api/health`)
      if (response.ok) return
      last = `HTTP ${response.status}`
    } catch (error) {
      last = String(error)
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`backend em ${BASE_URL} não ficou saudável: ${last}`)
}

function storageState(api: AgendaApi) {
  const { access_token, expires_at, user } = api.session
  return {
    cookies: [],
    origins: [
      {
        origin: BASE_URL,
        localStorage: [
          { name: AUTH_STORAGE_KEY, value: JSON.stringify({ token: access_token, expiresAt: expires_at, user }) },
        ],
      },
    ],
  }
}

function step(what: string, started: number): void {
  console.log(`[e2e] ${what} (${((Date.now() - started) / 1000).toFixed(1)} s)`)
}

export default async function globalSetup(_config: FullConfig): Promise<void> {
  if (!/^[a-z0-9_]+$/.test(DB_NAME)) throw new Error(`nome de banco inválido: ${DB_NAME}`)
  let started = Date.now()
  python(['-c', RECREATE_DB, PG_SERVER_URL, DB_NAME], `recriar o banco ${DB_NAME}`)
  step(`banco ${DB_NAME} recriado`, started)
  started = Date.now()
  python(['-m', 'app.cli', 'init-db'], 'init-db')
  await waitForHealth()
  step('migrations aplicadas', started)
  started = Date.now()

  const admin = await AgendaApi.login(USERS.admin.username, USERS.admin.password)
  const sessions: Partial<Record<UserKey, AgendaApi>> = { admin }
  try {
    const rooms = {} as Record<RoomKey, number>
    let order = 0
    for (const [key, room] of Object.entries(ROOMS) as [RoomKey, (typeof ROOMS)[RoomKey]][]) {
      const created = await admin.createRoom({
        name: room.name,
        room_type: room.type,
        capacity: room.capacity,
        resources: [...room.resources],
        display_order: ++order,
        is_active: true,
      })
      rooms[key] = created.id
    }

    const users = { admin: admin.session.user.id } as Record<UserKey, number>
    for (const key of ['ana', 'bia'] as const) {
      const u = USERS[key]
      const created = await admin.createUser({
        username: u.username,
        full_name: u.fullName,
        password: u.password,
        role: u.role,
      })
      users[key] = created.id
      sessions[key] = await AgendaApi.login(u.username, u.password)
    }

    const bookings: Record<string, string> = {}
    for (const b of SEED_BOOKINGS) {
      const created = await sessions[b.by]!.createBooking(bookingPayload(b, rooms[b.room]))
      bookings[b.key] = created.id
    }

    mkdirSync(STATE_DIR, { recursive: true })
    for (const [key, api] of Object.entries(sessions) as [UserKey, AgendaApi][]) {
      writeFileSync(path.join(STATE_DIR, `${key}.json`), JSON.stringify(storageState(api), null, 2))
    }
    const manifest: SeedManifest = { rooms, users, bookings }
    writeFileSync(SEED_MANIFEST, JSON.stringify(manifest, null, 2))
    step(`dados de teste cadastrados (${SEED_BOOKINGS.length} reservas)`, started)
  } finally {
    await Promise.all(Object.values(sessions).map((s) => s?.dispose()))
  }
}
