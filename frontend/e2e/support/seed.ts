/** Ids criados pelo global-setup (salas, usuários, reservas de leitura) e as sessões salvas. */
import { readFileSync } from 'node:fs'
import path from 'node:path'

import type { RoomKey, UserKey } from './data'
import { SEED_MANIFEST, STATE_DIR } from './env'

export interface SeedManifest {
  rooms: Record<RoomKey, number>
  users: Record<UserKey, number>
  /** SeedBooking.key → id da reserva. */
  bookings: Record<string, string>
}

let cached: SeedManifest | null = null

export function seed(): SeedManifest {
  cached ??= JSON.parse(readFileSync(SEED_MANIFEST, 'utf-8')) as SeedManifest
  return cached
}

export function storageStatePath(user: UserKey): string {
  return path.join(STATE_DIR, `${user}.json`)
}
