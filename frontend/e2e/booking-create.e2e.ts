import { DAYS, type RoomKey } from './support/data'
import { ticketUrl } from './support/env'
import { expect, test } from './support/fixtures'
import { Agenda, countClicks, roomName, tabLabel } from './support/grid'

test.describe('criação de reservas', () => {
  test('RNF03: um clique numa célula vazia e o resto só pelo teclado', async ({ ana, anaApi }) => {
    const date = DAYS.rnf03
    await anaApi.clearDay(date)
    const agenda = new Agenda(ana)
    await agenda.open(date)

    const clicks = await countClicks(ana)
    await agenda.clickCell('s06', '10:00')

    const composer = agenda.composer()
    await expect(composer).toBeVisible()
    await expect(composer.getByLabel('Nº do Chamado')).toBeFocused()
    await expect(composer).toContainText(`${roomName('s06')}${tabLabel(date)}10:00–10:30`)
    await ana.keyboard.type('910001')
    await ana.keyboard.press('Tab')
    await expect(composer.getByLabel('Solicitante')).toBeFocused()
    await ana.keyboard.type('Paula Andrade')
    await ana.keyboard.press('Enter')

    await expect(composer).toBeHidden()
    const block = agenda.blocks('s06', /^Chamado 910001, Paula Andrade, 10:00 às 10:30/)
    await expect(block).toHaveCount(1)
    await expect(block.getByRole('link', { name: '#910001' })).toHaveAttribute('href', ticketUrl('910001'))
    await expect(block.getByRole('link', { name: '#910001' })).toHaveAttribute('target', '_blank')
    await expect(agenda.toast('Reserva criada')).toBeVisible()
    await expect(agenda.toast(`${roomName('s06')} · ${tabLabel(date)} · 10:00–10:30 · Paula Andrade`)).toBeVisible()
    // RNF03: no máximo 3 cliques — aqui foi exatamente 1 (a célula); o resto, teclado.
    expect(await clicks()).toBe(1)

    // Persistida no servidor (e a trava da seleção foi liberada ao salvar).
    const [saved] = await anaApi.bookings(date)
    expect(saved).toMatchObject({
      ticket_number: '910001',
      requester_name: 'Paula Andrade',
      start: '10:00',
      end: '10:30',
    })
    await expect(ana.locator('[data-lock-id]')).toHaveCount(0)
  })

  test('arrastar sobre várias células seleciona a faixa', async ({ ana, anaApi, ids }) => {
    const date = DAYS.rnf03
    await anaApi.clearDay(date, { roomId: ids.rooms.s07 })
    const agenda = new Agenda(ana)
    await agenda.open(date)

    await agenda.dragCells('s07', '14:00', '15:30')
    const composer = agenda.composer()
    await expect(composer).toBeVisible()
    await expect(composer).toContainText('14:00–16:00')
    await expect(composer).toContainText('2 h')
    await expect(composer.getByLabel('Início')).toHaveValue('14:00')
    await expect(composer.getByLabel('Fim')).toHaveValue('16:00')
    await expect(composer.getByLabel('Nº do Chamado')).toBeFocused()
    await ana.keyboard.press('Tab')
    await ana.keyboard.type('Roberto Faixa')
    await ana.keyboard.press('Enter')

    await expect(composer).toBeHidden()
    const block = agenda.blocks('s07', /Roberto Faixa, 14:00 às 16:00/)
    await expect(block).toHaveCount(1)
    // Um único bloco cobrindo as 4 linhas da faixa.
    const top = (await agenda.cell('s07', '14:00').boundingBox())!.y
    const bottom = await agenda.cell('s07', '15:30').boundingBox()
    const box = (await block.boundingBox())!
    expect(Math.abs(box.y - top)).toBeLessThanOrEqual(3)
    expect(Math.abs(box.y + box.height - (bottom!.y + bottom!.height))).toBeLessThanOrEqual(3)
  })

  test('presets Dia Inteiro / Manhã / Tarde por clique e por Alt+2..4', async ({ ana, anaApi }) => {
    const date = DAYS.presets
    await anaApi.clearDay(date)
    const agenda = new Agenda(ana)
    await agenda.open(date)

    const cases: {
      room: RoomKey
      cell: string
      period: string
      how: 'click' | 'alt'
      key: string
      range: [string, string]
      flag?: string
    }[] = [
      {
        room: 's05',
        cell: '15:00',
        period: 'Dia Inteiro',
        how: 'click',
        key: '2',
        range: ['08:00', '19:00'],
        flag: 'Dia inteiro',
      },
      { room: 's06', cell: '09:00', period: 'Manhã', how: 'click', key: '3', range: ['08:00', '12:00'] },
      { room: 's07', cell: '16:00', period: 'Tarde', how: 'click', key: '4', range: ['13:00', '19:00'] },
      {
        room: 's08',
        cell: '12:00',
        period: 'Dia Inteiro',
        how: 'alt',
        key: '2',
        range: ['08:00', '19:00'],
        flag: 'Dia inteiro',
      },
      { room: 's10', cell: '08:30', period: 'Manhã', how: 'alt', key: '3', range: ['08:00', '12:00'] },
      { room: 's10', cell: '17:00', period: 'Tarde', how: 'alt', key: '4', range: ['13:00', '19:00'] },
    ]

    for (const c of cases) {
      const requester = `${c.period} ${c.how} ${c.room}`
      await agenda.clickCell(c.room, c.cell)
      const composer = agenda.composer()
      await expect(composer).toBeVisible()
      await ana.keyboard.press('Tab')
      await ana.keyboard.type(requester)
      const periods = composer.getByRole('radiogroup', { name: 'Tipo de Período' })
      if (c.how === 'click') await periods.getByRole('radio', { name: c.period }).click()
      else await ana.keyboard.press(`Alt+${c.key}`)
      await expect(periods.getByRole('radio', { name: c.period })).toHaveAttribute('aria-checked', 'true')
      await expect(composer).toContainText(`${c.range[0]}–${c.range[1]} · horário fixo do período`)
      // Com o mouse: célula + período + Salvar (3 cliques). Com o atalho, Enter no campo salva.
      if (c.how === 'click') await composer.getByRole('button', { name: 'Salvar', exact: true }).click()
      else await ana.keyboard.press('Enter')
      await expect(composer).toBeHidden()

      const block = agenda.blocks(c.room, new RegExp(`${requester}, ${c.range[0]} às ${c.range[1]}`))
      await expect(block).toHaveCount(1)
      if (c.flag) await expect(block).toHaveAccessibleName(new RegExp(`, ${c.flag}$`))
      // Bloco mesclado: do topo da primeira linha ao fim da última.
      const first = (await agenda.cell(c.room, c.range[0]).boundingBox())!
      const lastSlot = c.range[1] === '19:00' ? '18:30' : c.range[1] === '12:00' ? '11:30' : c.range[1]
      const last = (await agenda.cell(c.room, lastSlot).boundingBox())!
      const box = (await block.boundingBox())!
      expect(Math.abs(box.y - first.y)).toBeLessThanOrEqual(3)
      expect(Math.abs(box.y + box.height - (last.y + last.height))).toBeLessThanOrEqual(3)
    }

    const saved = await anaApi.bookings(date)
    expect(saved.map((b) => [b.room_name, b.period_type, b.start, b.end, b.is_full_day]).sort()).toEqual(
      [
        [roomName('s05'), 'FULL_DAY', '08:00', '19:00', true],
        [roomName('s06'), 'MORNING', '08:00', '12:00', false],
        [roomName('s07'), 'AFTERNOON', '13:00', '19:00', false],
        [roomName('s08'), 'FULL_DAY', '08:00', '19:00', true],
        [roomName('s10'), 'MORNING', '08:00', '12:00', false],
        [roomName('s10'), 'AFTERNOON', '13:00', '19:00', false],
      ].sort(),
    )
  })

  test('RNF03 só pelo teclado: setas, Shift+seta para a faixa, Enter, digitar e Enter', async ({ ana, anaApi }) => {
    const date = DAYS.keyboard
    await anaApi.clearDay(date)
    const agenda = new Agenda(ana)
    await agenda.open(date)
    await agenda.grid(date).focus()

    // A primeira seta só mostra o cursor (primeira sala, primeiro horário).
    await ana.keyboard.press('ArrowDown')
    for (let i = 0; i < 4; i++) await ana.keyboard.press('ArrowRight')
    await ana.keyboard.press('ArrowDown')
    await ana.keyboard.press('ArrowDown')
    await ana.keyboard.press('Shift+ArrowDown')
    await ana.keyboard.press('Shift+ArrowDown')
    await expect(agenda.column('s05').locator('[data-selection]')).toHaveText('09:00–10:30')
    await ana.keyboard.press('Enter')

    const composer = agenda.composer()
    await expect(composer).toBeVisible()
    await expect(composer).toContainText(`${roomName('s05')}${tabLabel(date)}09:00–10:30`)
    await expect(composer.getByLabel('Nº do Chamado')).toBeFocused()
    await ana.keyboard.type('910077')
    await ana.keyboard.press('Tab')
    await ana.keyboard.type('Teclado Puro')
    await ana.keyboard.press('Enter')
    await expect(composer).toBeHidden()
    const block = agenda.blocks('s05', /Chamado 910077, Teclado Puro, 09:00 às 10:30/)
    await expect(block).toHaveCount(1)

    // Enter com o cursor sobre a reserva abre os detalhes; Esc fecha.
    await ana.keyboard.press('Enter')
    const popover = agenda.popover('Teclado Puro')
    await expect(popover).toBeVisible()
    await ana.keyboard.press('Escape')
    await expect(popover).toBeHidden()

    // Esc no modal cancela sem gravar e libera a seleção.
    await ana.keyboard.press('ArrowRight')
    await ana.keyboard.press('Enter')
    await expect(composer).toBeVisible()
    await ana.keyboard.press('Escape')
    await expect(composer).toBeHidden()
    await expect(ana.locator('[data-selection]')).toHaveCount(0)
    expect(await anaApi.bookings(date)).toHaveLength(1)
  })
})
