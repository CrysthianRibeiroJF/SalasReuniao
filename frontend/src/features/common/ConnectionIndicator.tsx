import { Tooltip } from '../../components/ui/Tooltip'
import { cn } from '../../lib/cn'
import type { ConnectionStatus } from '../../realtime/socket'
import { useRealtime } from '../../store/realtime'

const COPY: Record<ConnectionStatus, { label: string; short: string; hint: string; dot: string; text: string }> = {
  open: {
    label: 'Tempo real conectado',
    short: 'Conectado',
    hint: 'Alterações das colegas aparecem na hora.',
    dot: 'bg-success',
    text: 'text-success',
  },
  connecting: {
    label: 'Conectando…',
    short: 'Conectando…',
    hint: 'Abrindo a conexão em tempo real.',
    dot: 'bg-warning animate-pulse-dot',
    text: 'text-warning',
  },
  reconnecting: {
    label: 'Reconectando…',
    short: 'Reconectando…',
    hint: 'A conexão caiu. Tentando de novo; a grade será recarregada ao voltar.',
    dot: 'bg-warning animate-pulse-dot',
    text: 'text-warning',
  },
  idle: {
    label: 'Desconectado',
    short: 'Offline',
    hint: 'Sem conexão em tempo real.',
    dot: 'bg-faint',
    text: 'text-muted',
  },
  closed: {
    label: 'Desconectado',
    short: 'Offline',
    hint: 'Sem conexão em tempo real.',
    dot: 'bg-faint',
    text: 'text-muted',
  },
}

const POLLING_COPY = {
  label: 'Atualização automática',
  short: 'Atualizando',
  hint: 'O tempo real está bloqueado nesta rede: a grade é atualizada a cada poucos segundos e o aviso "Reservando" não aparece. Reservas duplicadas continuam impedidas.',
  dot: 'bg-warning',
  text: 'text-warning',
}

export function ConnectionIndicator() {
  const status = useRealtime((s) => s.status)
  const polling = useRealtime((s) => s.polling)
  const copy = polling && status !== 'open' ? POLLING_COPY : COPY[status]
  return (
    <Tooltip content={copy.hint}>
      <span
        role="status"
        aria-label={copy.label}
        className={cn('inline-flex items-center gap-1.5 text-[12px] font-medium whitespace-nowrap', copy.text)}
      >
        <span className={cn('size-2 rounded-full', copy.dot)} aria-hidden="true" />
        <span className="hidden 2xl:inline">{copy.label}</span>
        <span className="2xl:hidden">{copy.short}</span>
      </span>
    </Tooltip>
  )
}
