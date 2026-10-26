/**
 * Dados determinísticos dos testes E2E (criados pelo global-setup através da API REST).
 *
 * Senhas de TESTE — existem só no banco agenda_e2e, recriado a cada execução.
 *
 * Cada teste que grava reservas usa um dia próprio (DAYS) em semanas futuras fixas, para não
 * depender de "hoje" nem disputar a grade com outros testes rodando em paralelo. Os testes que só
 * leem (abas, busca, filtros, exportação, layout) usam as reservas de SEED_BOOKINGS — com nomes que
 * não aparecem na planilha legada de exemplo, que o teste de importação grava no mesmo banco.
 */
import type { BookingCreate, PeriodType, RoomType, UserRole } from '../../src/api/types'

export interface TestUser {
  key: 'admin' | 'ana' | 'bia'
  username: string
  password: string
  fullName: string
  role: UserRole
}

export const USERS = {
  admin: {
    key: 'admin',
    username: 'admin',
    password: 'admin-e2e-2026',
    fullName: 'Administrador E2E',
    role: 'ADMIN',
  },
  ana: { key: 'ana', username: 'ana', password: 'ana-e2e-2026', fullName: 'Ana Recepção', role: 'RECEPTIONIST' },
  bia: { key: 'bia', username: 'bia', password: 'bia-e2e-2026', fullName: 'Bia Recepção', role: 'RECEPTIONIST' },
} as const satisfies Record<string, TestUser>

export type UserKey = keyof typeof USERS

/** Mesmos nomes da planilha legada de exemplo (docs/exemplos), para a importação casar as salas. */
export const ROOMS = {
  s01: { name: 'Sala 01 - Executiva', type: 'VIDEO', capacity: 12, resources: ['Videoconferência', 'TV 75"'] },
  s02: { name: 'Sala 02 - Diretoria', type: 'VIDEO', capacity: 10, resources: ['Videoconferência', 'TV 65"'] },
  s03: { name: 'Sala 03 - Treinamento', type: 'VIDEO', capacity: 20, resources: ['Videoconferência', 'Projetor'] },
  /** A sala de vídeo que o teste de manutenção (RF02) usa — nenhum outro teste depende dela. */
  s04: { name: 'Sala 04 - Inovação', type: 'VIDEO', capacity: 8, resources: ['Videoconferência', 'Tela interativa'] },
  s05: { name: 'Sala 05 - Reunião', type: 'NORMAL', capacity: 6, resources: ['TV 50"'] },
  s06: { name: 'Sala 06 - Reunião', type: 'NORMAL', capacity: 6, resources: ['Quadro branco'] },
  s07: { name: 'Sala 07 - Atendimento', type: 'NORMAL', capacity: 4, resources: [] },
  s08: { name: 'Sala 08 - Projetos', type: 'NORMAL', capacity: 8, resources: ['Quadro branco', 'Flip chart'] },
  s09: { name: 'Sala 09 - Auditório', type: 'NORMAL', capacity: 40, resources: ['Projetor', 'Microfone'] },
  s10: { name: 'Sala 10 - Entrevistas', type: 'NORMAL', capacity: 3, resources: [] },
} as const satisfies Record<string, { name: string; type: RoomType; capacity: number; resources: readonly string[] }>

export type RoomKey = keyof typeof ROOMS

/** Dias dedicados (todos no futuro em relação a 25/10/2026). */
export const DAYS = {
  // Semana A (somente leitura): abas e contagens, busca, filtros
  weekA: { mon: '2026-11-09', tue: '2026-11-10', wed: '2026-11-11', thu: '2026-11-12', fri: '2026-11-13' },
  weekASaturday: '2026-11-14',
  // Semana B: criação de reservas
  rnf03: '2026-11-16',
  presets: '2026-11-17',
  fullDayConflict: '2026-11-18',
  videoRooms: '2026-11-19',
  popover: '2026-11-20',
  // Semana C: tempo real e manutenção
  realtimeCrud: '2026-11-23',
  realtimeLocks: '2026-11-24',
  staleEdit: '2026-11-25',
  reconnect: '2026-11-26',
  maintenance: '2026-11-27',
  // Semana D: Excel, layout, só teclado, edição que muda de dia, arrastar, avisos do modal
  export: '2026-11-30',
  layout: '2026-12-01',
  moveTarget: '2026-12-02',
  keyboard: '2026-12-03',
  editing: '2026-12-04',
  // Semana E: desativação de sala com reservas futuras (D1), ciclo de vida do modal (R8/R16) e a trava
  // do Dia Inteiro enquanto a reunião conflitante é remanejada (5.2, opção 2 — D6)
  roomDeactivation: '2026-12-09',
  composerLifecycle: '2026-12-10',
  fullDayRelocateLock: '2026-12-11',
  /** Dia passado: reserva antiga numa sala desativada (a coluna "Sala desativada" só aparece nele). */
  inactiveRoomPast: '2026-10-13',
} as const

/** Último dia usado pelos testes (dias livres depois dele ficam para a importação, se precisar). */
export const LAST_TEST_DAY = DAYS.fullDayRelocateLock

export interface SeedBooking {
  key: string
  room: RoomKey
  date: string
  period?: PeriodType
  start?: string
  end?: string
  requester: string
  title?: string
  ticket?: string
  requiresVideo?: boolean
  attendees?: number
  by: 'ana' | 'bia'
}

const { mon, tue, wed, fri } = DAYS.weekA

// prettier-ignore
export const SEED_BOOKINGS: readonly SeedBooking[] = [
  // Semana A — Seg 09/11: 3 reservas
  { key: 'carla', room: 's01', date: mon, start: '09:00', end: '10:30', requester: 'Carla Vasconcelos', title: 'Diretoria Financeira', ticket: '700101', requiresVideo: true, by: 'ana' },
  { key: 'marcos', room: 's09', date: mon, period: 'FULL_DAY', requester: 'Marcos Albuquerque', title: 'Integração de novos colaboradores', ticket: '700102', by: 'bia' },
  { key: 'joao', room: 's05', date: mon, start: '14:00', end: '15:00', requester: 'João Brandão', title: 'RH - Entrevista', ticket: '700103', by: 'ana' },
  // Ter 10/11: 2
  { key: 'fernanda', room: 's02', date: tue, period: 'MORNING', requester: 'Fernanda Quintela', title: 'Planejamento Estratégico', ticket: '700201', requiresVideo: true, by: 'ana' },
  { key: 'ti', room: 's06', date: tue, start: '10:00', end: '11:00', requester: 'Equipe de Infraestrutura', title: 'Reunião interna', by: 'bia' },
  // Qua 11/11: 1
  { key: 'sonia', room: 's07', date: wed, start: '15:00', end: '16:30', requester: 'Sônia Araújo', title: 'Atendimento a fornecedor', ticket: '700301', by: 'bia' },
  // Qui 12/11: nenhuma
  // Sex 13/11: 4 (a última fica no canto inferior direito da grade: a busca precisa rolar até ela)
  { key: 'rafael', room: 's01', date: fri, start: '10:00', end: '11:00', requester: 'Rafael Damasceno', title: 'Alinhamento semanal', ticket: '700501', by: 'ana' },
  { key: 'eduardo', room: 's03', date: fri, start: '14:00', end: '18:00', requester: 'Eduardo Gusmão', title: 'Treinamento Teams', ticket: '700502', requiresVideo: true, by: 'bia' },
  { key: 'juliana', room: 's08', date: fri, period: 'AFTERNOON', requester: 'Juliana Peçanha', title: 'Workshop de Processos', ticket: '700504', by: 'ana' },
  { key: 'camila', room: 's10', date: fri, start: '17:30', end: '18:30', requester: 'Camila Teixeira', title: 'Entrevista - Estágio', ticket: '700503', by: 'bia' },
  // Semana D — exportação (Seg 30/11)
  { key: 'export1', room: 's02', date: DAYS.export, start: '09:00', end: '10:00', requester: 'Helena Export', title: 'Comitê de Exportação', ticket: '730001', requiresVideo: true, by: 'ana' },
  { key: 'export2', room: 's06', date: DAYS.export, period: 'AFTERNOON', requester: 'Igor Planilha', title: 'Treinamento Excel', ticket: '730002', by: 'bia' },
  // Layout/capturas (Ter 01/12): um pouco de tudo na grade
  { key: 'layout1', room: 's01', date: DAYS.layout, start: '08:30', end: '10:00', requester: 'Diretoria Executiva', title: 'Reunião de diretoria', ticket: '740001', requiresVideo: true, attendees: 9, by: 'ana' },
  { key: 'layout2', room: 's02', date: DAYS.layout, start: '13:00', end: '15:00', requester: 'Treinamento Comercial', title: 'Presencial em sala de vídeo', ticket: '740002', by: 'bia' },
  { key: 'layout3', room: 's05', date: DAYS.layout, period: 'MORNING', requester: 'RH Seleção', title: 'Entrevistas', ticket: '740003', by: 'ana' },
  { key: 'layout4', room: 's09', date: DAYS.layout, period: 'FULL_DAY', requester: 'Integração', title: 'Novos colaboradores', ticket: '740004', attendees: 30, by: 'bia' },
  { key: 'layout5', room: 's07', date: DAYS.layout, start: '15:00', end: '16:00', requester: 'Atendimento Fornecedor', ticket: '740005', by: 'ana' },
  { key: 'layout6', room: 's03', date: DAYS.layout, start: '10:30', end: '12:00', requester: 'Squad Produtos', title: 'Daily estendida', ticket: '740006', requiresVideo: true, by: 'bia' },
]

/** Contagem esperada de reservas nas abas da semana A. */
export const WEEK_A_COUNTS: Record<string, number> = {
  [DAYS.weekA.mon]: 3,
  [DAYS.weekA.tue]: 2,
  [DAYS.weekA.wed]: 1,
  [DAYS.weekA.thu]: 0,
  [DAYS.weekA.fri]: 4,
}

export function bookingPayload(b: SeedBooking, roomId: number): BookingCreate {
  const period = b.period ?? 'CUSTOM'
  return {
    room_id: roomId,
    date: b.date,
    period_type: period,
    ...(period === 'CUSTOM' ? { start: b.start, end: b.end } : {}),
    ticket_number: b.ticket ?? null,
    title: b.title ?? null,
    requester_name: b.requester,
    requires_video: b.requiresVideo ?? false,
    attendees: b.attendees ?? null,
  }
}
