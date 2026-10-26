import { DAYS } from './support/data'
import { expect, test } from './support/fixtures'
import { Agenda, capture } from './support/grid'

test('reconexão: sem rede mostra "Reconectando…" e, ao voltar, a grade traz o que mudou', async ({
  ana,
  bia,
  anaApi,
  ids,
}) => {
  const date = DAYS.reconnect
  await anaApi.clearDay(date)
  const a = new Agenda(ana)
  const b = new Agenda(bia)
  await a.open(date)
  await b.open(date)

  await ana.context().setOffline(true)
  await expect(a.connection('Reconectando…')).toBeVisible()
  await expect(a.connection('Reconectando…')).toHaveText('Reconectando…', { useInnerText: true })

  // Enquanto a Ana está sem rede, a Bia cria uma reserva (pela grade) e outra é criada pela API.
  await b.clickCell('s05', '13:00')
  await expect(b.composer()).toBeVisible()
  await bia.keyboard.press('Tab')
  await bia.keyboard.type('Criada durante a queda')
  await bia.keyboard.press('Enter')
  await expect(b.composer()).toBeHidden()
  const viaApi = await anaApi.createBooking({
    room_id: ids.rooms.s06,
    date,
    period_type: 'MORNING',
    requester_name: 'Também durante a queda',
  })
  await expect(a.blocks('s05')).toHaveCount(0)
  // O modal avisa que as colegas não veem a seleção enquanto não houver tempo real.
  await a.clickCell('s08', '15:00')
  await expect(a.composer()).toContainText('Sem tempo real')
  await capture(ana, 'sem-tempo-real')
  await ana.keyboard.press('Escape')

  await ana.context().setOffline(false)
  await expect(a.connection('Tempo real conectado')).toBeVisible({ timeout: 20_000 })
  await expect(a.blocks('s05', /Criada durante a queda, 13:00 às 13:30/)).toHaveCount(1)
  await expect(a.block(viaApi.id)).toBeVisible()

  // E o tempo real volta a funcionar normalmente.
  await b.block(viaApi.id).click()
  const popover = b.popover('Também durante a queda')
  await popover.getByRole('button', { name: 'Check-in' }).click()
  await expect(a.block(viaApi.id)).toHaveAccessibleName(/, Check-in$/)
})

test('queda com o modal aberto: a trava é refeita ao reconectar e a reserva salva normalmente', async ({
  ana,
  bia,
  anaApi,
}) => {
  const date = DAYS.reconnect
  await anaApi.clearDay(date)
  const a = new Agenda(ana)
  const b = new Agenda(bia)
  await a.open(date)
  await b.open(date)

  await a.clickCell('s09', '10:00')
  const composer = a.composer()
  await expect(composer).toBeVisible()
  await expect(b.locks('s09')).toHaveCount(1)
  await ana.keyboard.press('Tab')
  await ana.keyboard.type('Atravessou a queda')

  // Sem rede: o servidor libera a trava da conexão que caiu e a Bia deixa de ver a seleção.
  await ana.context().setOffline(true)
  await expect(composer).toContainText('Sem tempo real')
  await expect(b.locks('s09')).toHaveCount(0)

  // Volta a rede: o modal (ainda aberto) trava de novo a mesma faixa.
  await ana.context().setOffline(false)
  await expect(a.connection('Tempo real conectado')).toBeVisible({ timeout: 20_000 })
  await expect(b.locks('s09')).toHaveCount(1)
  await expect(b.locks('s09')).toHaveAttribute('title', 'Ana Recepção está reservando 10:00–10:30')
  await expect(composer.getByLabel('Solicitante')).toHaveValue('Atravessou a queda')
  await composer.getByLabel('Solicitante').press('Enter')
  await expect(composer).toBeHidden()
  await expect(b.blocks('s09', /Atravessou a queda, 10:00 às 10:30/)).toHaveCount(1)
  await expect(b.locks('s09')).toHaveCount(0)
})
