import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'

import type { Download } from '@playwright/test'

import { DAYS, LAST_TEST_DAY } from './support/data'
import { BACKEND_DIR, PYTHON, REPO_DIR, SCREENSHOTS_DIR } from './support/env'
import { expect, test } from './support/fixtures'
import { Agenda, tabLabel } from './support/grid'
import { report, watch } from './support/timing'

const SAMPLE = path.join(REPO_DIR, 'docs', 'exemplos', 'planilha-legada-exemplo.xlsx')
/** Segunda-feira da semana da planilha de exemplo. */
const SAMPLE_MONDAY = '2026-10-26'

/** Abas e textos de uma planilha .xlsx (lida com openpyxl, no Python do backend). */
function readWorkbook(file: string): { sheets: string[]; texts: string[] } {
  const script = `
import json, sys
import openpyxl
wb = openpyxl.load_workbook(sys.argv[1])
texts = [str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value is not None]
print(json.dumps({"sheets": wb.sheetnames, "texts": texts}, ensure_ascii=False))
`
  const out = execFileSync(PYTHON, ['-c', script, file], {
    cwd: BACKEND_DIR,
    encoding: 'utf-8',
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  })
  return JSON.parse(out) as { sheets: string[]; texts: string[] }
}

async function saveDownload(download: Download, dir: string): Promise<string> {
  const file = path.join(dir, download.suggestedFilename())
  await download.saveAs(file)
  return file
}

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}

function nextMonday(iso: string): string {
  const weekday = new Date(`${iso}T12:00:00Z`).getUTCDay()
  return addDays(iso, (8 - weekday) % 7 || 7)
}

/** Nome das abas da planilha de exemplo: "Seg 26-10". */
function sheetName(prefix: string, iso: string): string {
  return `${prefix} ${iso.slice(8, 10)}-${iso.slice(5, 7)}`
}

test('exportar dia e semana em .xlsx', async ({ ana }, testInfo) => {
  const agenda = new Agenda(ana)
  await agenda.open(DAYS.export, { realtime: false })
  const exportGroup = ana.getByRole('group', { name: 'Exportar para Excel' })

  const [day] = await Promise.all([
    ana.waitForEvent('download'),
    exportGroup.getByRole('button', { name: 'Dia' }).click(),
  ])
  expect(day.suggestedFilename()).toBe(`agenda-${DAYS.export}.xlsx`)
  const dayFile = await saveDownload(day, testInfo.outputPath())
  expect(readFileSync(dayFile).subarray(0, 2).toString('latin1')).toBe('PK')
  await expect(agenda.toast('Planilha baixada')).toBeVisible()
  const dayBook = readWorkbook(dayFile)
  expect(dayBook.texts.join('\n')).toContain('Helena Export')
  expect(dayBook.texts.join('\n')).toContain('Igor Planilha')

  const [week] = await Promise.all([
    ana.waitForEvent('download'),
    exportGroup.getByRole('button', { name: 'Semana' }).click(),
  ])
  expect(week.suggestedFilename()).toBe(`agenda-semana-${DAYS.export}.xlsx`)
  const weekFile = await saveDownload(week, testInfo.outputPath())
  expect(readFileSync(weekFile).subarray(0, 2).toString('latin1')).toBe('PK')
  // Uma aba por dia útil, no formato "Seg 30-11".
  expect(readWorkbook(weekFile).sheets).toEqual(['Seg 30-11', 'Ter 01-12', 'Qua 02-12', 'Qui 03-12', 'Sex 04-12'])
})

test('admin importa a planilha legada (simulação → importar de verdade), a Bia vê sem recarregar e reimportar não duplica nem ressuscita', async ({
  adminPage,
  adminApi,
  bia,
  biaApi,
  ids,
}, testInfo) => {
  // A planilha de exemplo é da semana de 26/10/2026 e só datas de hoje em diante entram. Se esse dia
  // já passou, geramos a mesma planilha com o script do próprio repositório para uma semana futura
  // depois dos dias dos outros testes (inclusive a quinta-feira "antiga" que ela traz, monday - 4).
  const today = await biaApi.today()
  let file = SAMPLE
  let monday = SAMPLE_MONDAY
  if (today > SAMPLE_MONDAY) {
    monday = nextMonday(addDays(today > LAST_TEST_DAY ? today : LAST_TEST_DAY, 4))
    file = testInfo.outputPath('planilha-legada.xlsx')
    execFileSync(PYTHON, ['scripts/make_sample_legacy.py', '--monday', monday, '-o', file], { cwd: BACKEND_DIR })
  }
  await biaApi.clearDay(monday)
  // A planilha traz também uma quinta-feira anterior (monday - 4): ela só é ignorada se já passou.
  const pastThursday = addDays(monday, -4)
  const thursdayIsPast = pastThursday < today
  const expected = { found: thursdayIsPast ? 20 : 21, imported: thursdayIsPast ? 16 : 17, skipped: 4 }

  const b = new Agenda(bia)
  await b.open(monday)
  await expect(bia.locator('[data-booking-id]')).toHaveCount(0)

  const page = adminPage
  await page.goto('/admin/importar')
  await expect(page.getByRole('heading', { name: 'Importar planilha', level: 1 })).toBeVisible()
  await page.locator('#import-file').setInputFiles(file)
  await expect(page.getByText(path.basename(file))).toBeVisible()
  await expect(page.getByRole('switch', { name: /Incluir datas passadas/ })).toHaveAttribute('aria-checked', 'false')
  await page.getByRole('button', { name: 'Simular importação' }).click()

  const simulation = page.getByRole('region', { name: 'Resultado da simulação' })
  await expect(simulation.getByRole('heading', { name: 'Simulação — nada foi gravado' })).toBeVisible()
  const stat = (label: string) => simulation.locator('dt', { hasText: label }).locator('xpath=following-sibling::dd')
  await expect(stat('Reservas encontradas')).toHaveText(String(expected.found))
  await expect(stat('Seriam importadas')).toHaveText(String(expected.imported))
  await expect(stat('Puladas')).toHaveText(String(expected.skipped))
  await expect(simulation.getByText('Sala 11 - Copa')).toBeVisible()
  const row = (sheet: string) =>
    simulation.getByRole('row').filter({ has: page.getByRole('cell', { name: sheet, exact: true }) })
  // A situação aponta para o aviso, que traz o motivo (aqui, aba sem data).
  await expect(row('LEIA-ME')).toContainText('Ignorada (veja o aviso)')
  await row('LEIA-ME').getByRole('button', { name: '1 aviso' }).click()
  await expect(simulation.getByText(/^Data não identificada: use o nome da aba/)).toBeVisible()
  await expect(row(sheetName('Qui', pastThursday))).toContainText(
    thursdayIsPast ? 'Ignorada (data passada)' : 'Simulada',
  )
  await expect(row(sheetName('Seg', monday)).getByRole('cell')).toHaveText([
    sheetName('Seg', monday),
    tabLabel(monday),
    'Simulada',
    '5',
    '4',
    '1',
    '1 aviso',
  ])
  // Simulação não grava nada.
  expect(await biaApi.bookings(monday)).toHaveLength(0)
  await page.screenshot({ path: path.join(SCREENSHOTS_DIR, 'importacao-simulacao.png'), fullPage: true })

  await page.getByRole('button', { name: 'Importar de verdade' }).click()
  const confirm = page.getByRole('dialog', { name: 'Importar de verdade?' })
  await expect(confirm).toContainText(path.basename(file))
  const appears = watch(
    bia,
    (room) => !!document.querySelector(`[data-room-id="${room}"] [data-booking-id][aria-label*="Carla Mendes"]`),
    ids.rooms.s01,
    15_000,
  )
  appears.start()
  await confirm.getByRole('button', { name: 'Importar agora' }).click()

  const result = page.getByRole('region', { name: 'Resultado da importação' })
  await expect(result.getByRole('heading', { name: 'Importação concluída' })).toBeVisible()
  await expect(result.locator('dt', { hasText: 'Importadas' }).locator('xpath=following-sibling::dd')).toHaveText(
    String(expected.imported),
  )
  await expect(
    page.locator('[aria-live="polite"]').getByText(`${expected.imported} reserva(s) gravada(s).`),
  ).toBeVisible()

  // Bia (outra sessão, no dia da planilha) vê as reservas importadas sem recarregar.
  report(testInfo, 'importação (admin confirma → reservas na tela da Bia)', await appears.done())
  await expect(bia.locator('[data-booking-id]')).toHaveCount(4)
  await expect(b.blocks('s09', /Marcos Silva/)).toHaveAccessibleName(/08:00 às 19:00, Dia inteiro$/)
  await expect(b.tab(monday)).toHaveAccessibleName(/, 4 reservas/)
  const imported = await biaApi.bookings(monday)
  expect(imported).toHaveLength(4)
  expect((await biaApi.logs(imported[0]!.id)).map((l) => l.action)).toEqual(['IMPORTED'])

  // Importar de novo a mesma planilha não duplica: tudo é pulado.
  await page.getByRole('button', { name: 'Importar outra planilha' }).click()
  await page.locator('#import-file').setInputFiles(file)
  await page.getByRole('button', { name: 'Simular importação' }).click()
  await expect(stat('Seriam importadas')).toHaveText('0')
  await expect(page.getByRole('button', { name: 'Importar de verdade' })).toBeDisabled()

  // D10: uma reserva importada e depois cancelada no sistema não volta ao importar a planilha de novo.
  const carla = imported.find((booking) => booking.requester_name.includes('Carla Mendes'))!
  await biaApi.cancelBooking(carla.id, 'cancelada pela recepção (teste E2E)')
  await expect(b.block(carla.id)).toHaveCount(0)
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/import/xlsx?dry_run=true') && r.ok()),
    page.getByRole('button', { name: 'Simular de novo' }).click(),
  ])
  await expect(stat('Seriam importadas')).toHaveText('0')
  await row(sheetName('Seg', monday))
    .getByRole('button', { name: /avisos?$/ })
    .click()
  await expect(
    simulation.getByText(
      /^1 reserva\(s\) já importada\(s\) anteriormente \(pode ter sido movida ou cancelada no sistema\) — pulada\(s\)/,
    ),
  ).toBeVisible()
  await expect(page.getByRole('button', { name: 'Importar de verdade' })).toBeDisabled()
  // Nem uma importação de verdade (forçada pela API) a traz de volta.
  const forced = await adminApi.importXlsx(file, { dryRun: false })
  expect(forced.total_imported).toBe(0)
  expect((await biaApi.booking(carla.id)).status).toBe('CANCELLED')
  const remaining = imported.filter((booking) => booking.id !== carla.id).map((booking) => booking.id)
  expect((await biaApi.bookings(monday)).map((booking) => booking.id).sort()).toEqual(remaining.sort())
  await expect(bia.locator('[data-booking-id]')).toHaveCount(3)
})
