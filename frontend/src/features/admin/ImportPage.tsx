import { type DragEvent, useRef, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  FileSpreadsheet,
  FlaskConical,
  RotateCcw,
  Upload,
} from 'lucide-react'

import { errorMessage } from '../../api/errors'
import { api } from '../../api/endpoints'
import type { ImportReport, ImportSheetReport, ImportSheetStatus } from '../../api/types'
import { Badge, type BadgeTone } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Dialog } from '../../components/ui/Dialog'
import { Switch } from '../../components/ui/Switch'
import { cn } from '../../lib/cn'
import { IMPORT_STATUS_LABELS } from '../../lib/labels'
import { formatTabLabel } from '../../lib/time'
import { useBookings } from '../../store/bookings'
import { toast } from '../../store/toasts'
import { DataTable, PageHeader, Td, Th } from './AdminLayout'

const STATUS_TONES: Record<ImportSheetStatus, BadgeTone> = {
  IMPORTED: 'success',
  DRY_RUN: 'video',
  SKIPPED_PAST: 'neutral',
  SKIPPED_UNRECOGNIZED: 'warning',
  ERROR: 'danger',
}

/** Mesmo limite do servidor (MAX_UPLOAD_BYTES em backend/app/api/excel.py). */
export const MAX_UPLOAD_MB = 10

const megabytes = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })

export function formatSize(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${megabytes.format(bytes / 1024 / 1024)} MB`
}

type Step = 'select' | 'simulated' | 'imported'

/** Importação da planilha legada: escolher → simular (dry-run) → importar de verdade. */
export function ImportPage() {
  const [file, setFile] = useState<File | null>(null)
  const [includePast, setIncludePast] = useState(false)
  const [report, setReport] = useState<ImportReport | null>(null)
  /** Opções com que a simulação mostrada foi feita: a importação real usa exatamente estas (R34). */
  const [simulatedWith, setSimulatedWith] = useState<{ includePast: boolean } | null>(null)
  const [step, setStep] = useState<Step>('select')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const pick = (candidate: File | undefined) => {
    setError(null)
    if (!candidate) return
    if (!/\.xlsx$/i.test(candidate.name)) {
      setError('Escolha um arquivo .xlsx (Excel). Arquivos .xls antigos precisam ser salvos como .xlsx antes.')
      return
    }
    if (candidate.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setError(`O arquivo tem ${formatSize(candidate.size)}; o limite é ${MAX_UPLOAD_MB} MB.`)
      return
    }
    setFile(candidate)
    setReport(null)
    setSimulatedWith(null)
    setStep('select')
  }

  const run = async (dryRun: boolean) => {
    if (!file) return
    // "Importar de verdade" grava o que a simulação mostrou: mesmas opções da simulação.
    const options = dryRun || !simulatedWith ? { includePast } : simulatedWith
    setBusy(true)
    setError(null)
    try {
      const result = await api.excel.importXlsx(file, { dryRun, ...options })
      setReport(result)
      setSimulatedWith(dryRun ? options : null)
      setStep(dryRun ? 'simulated' : 'imported')
      if (!dryRun) {
        toast.success('Importação concluída', { description: `${result.total_imported} reserva(s) gravada(s).` })
        void useBookings.getState().refetchLoaded()
      }
    } catch (err) {
      setError(errorMessage(err, 'Não foi possível processar a planilha.'))
    } finally {
      setBusy(false)
      setConfirming(false)
    }
  }

  const reset = () => {
    setFile(null)
    setReport(null)
    setSimulatedWith(null)
    setStep('select')
    setError(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    setDragOver(false)
    pick(event.dataTransfer.files[0])
  }

  return (
    <>
      <PageHeader
        title="Importar planilha"
        description="Traga as abas diárias da planilha atual para o sistema. A simulação mostra o que seria importado sem gravar nada; só “Importar de verdade” grava."
      />

      <ol className="mb-5 flex flex-wrap items-center gap-2 text-[12.5px]" aria-label="Etapas">
        {(
          [
            ['select', 'Escolher arquivo'],
            ['simulated', 'Conferir simulação'],
            ['imported', 'Importar'],
          ] as const
        ).map(([key, label], i, all) => {
          const index = all.findIndex(([k]) => k === step)
          const state = i < index ? 'done' : i === index ? 'current' : 'todo'
          return (
            <li key={key} className="flex items-center gap-2">
              <span
                className={cn(
                  'flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-medium',
                  state === 'current' && 'border-ink bg-ink text-white',
                  state === 'done' && 'border-success/30 bg-success-tint text-success',
                  state === 'todo' && 'border-line-strong text-muted',
                )}
                aria-current={state === 'current' ? 'step' : undefined}
              >
                <span className="font-mono">{i + 1}</span> {label}
              </span>
              {i < all.length - 1 && <ChevronRight className="size-3.5 text-faint" aria-hidden="true" />}
            </li>
          )
        })}
      </ol>

      {step !== 'imported' && (
        <section className="rounded-lg border border-line-strong bg-surface p-4">
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={cn(
              'flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors',
              dragOver ? 'border-video bg-video-tint' : 'border-line-strong bg-paper/60',
            )}
          >
            <FileSpreadsheet className="size-8 text-normal" aria-hidden="true" />
            {file ? (
              <p className="text-[13.5px]">
                <span className="font-semibold text-ink">{file.name}</span>{' '}
                <span className="text-muted">· {formatSize(file.size)}</span>
              </p>
            ) : (
              <p className="text-[13.5px] text-ink-2">Arraste a planilha .xlsx para cá ou</p>
            )}
            <input
              ref={inputRef}
              id="import-file"
              type="file"
              accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              className="sr-only"
              onChange={(e) => pick(e.target.files?.[0])}
            />
            <Button size="sm" icon={<Upload className="size-3.5" />} onClick={() => inputRef.current?.click()}>
              {file ? 'Trocar arquivo' : 'Escolher arquivo'}
            </Button>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-4">
            <Switch
              checked={includePast}
              onChange={(value) => {
                setIncludePast(value)
                // A simulação mostrada não vale mais para a nova opção: é preciso simular de novo.
                if (report?.dry_run) {
                  setReport(null)
                  setSimulatedWith(null)
                  setStep('select')
                }
              }}
              tone="ink"
              label="Incluir datas passadas"
              description={
                report?.dry_run
                  ? 'Mudar esta opção descarta a simulação atual.'
                  : 'Por padrão só abas de hoje em diante são importadas.'
              }
              className="max-w-sm"
            />
            <Button
              variant={step === 'simulated' ? 'secondary' : 'primary'}
              icon={<FlaskConical className="size-4" />}
              disabled={!file}
              loading={busy && !confirming}
              onClick={() => void run(true)}
            >
              {step === 'simulated' ? 'Simular de novo' : 'Simular importação'}
            </Button>
          </div>
          {error && (
            <p
              role="alert"
              className="mt-3 rounded-md border border-danger/25 bg-danger-tint px-3 py-2 text-[12.5px] text-danger-ink"
            >
              {error}
            </p>
          )}
        </section>
      )}

      {report && (
        <section
          className="mt-5 space-y-4"
          aria-label={report.dry_run ? 'Resultado da simulação' : 'Resultado da importação'}
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold text-ink">
              {report.dry_run ? (
                <>
                  <FlaskConical className="size-4 text-video" aria-hidden="true" /> Simulação — nada foi gravado
                </>
              ) : (
                <>
                  <CheckCircle2 className="size-4 text-success" aria-hidden="true" /> Importação concluída
                </>
              )}
            </h2>
            {report.dry_run ? (
              <Button
                variant="primary"
                disabled={report.total_found - report.total_skipped <= 0}
                onClick={() => setConfirming(true)}
              >
                Importar de verdade
              </Button>
            ) : (
              <Button icon={<RotateCcw className="size-4" />} onClick={reset}>
                Importar outra planilha
              </Button>
            )}
          </div>
          <ReportView report={report} />
        </section>
      )}

      <Dialog
        open={confirming}
        onClose={() => setConfirming(false)}
        busy={busy}
        size="sm"
        title="Importar de verdade?"
        description={file?.name}
        footer={
          <>
            <Button onClick={() => setConfirming(false)} disabled={busy}>
              Voltar
            </Button>
            <Button variant="primary" loading={busy} onClick={() => void run(false)}>
              Importar agora
            </Button>
          </>
        }
      >
        <p className="text-[13px] text-ink-2">
          As reservas da simulação serão gravadas e aparecerão na grade de todas as recepcionistas. Reservas já
          cadastradas e as que vieram de uma importação anterior (mesmo movidas ou canceladas depois) são puladas, então
          importar de novo não duplica nem traz de volta o que foi cancelado.
        </p>
      </Dialog>
    </>
  )
}

export function ReportView({ report }: { report: ImportReport }) {
  const toImport = report.dry_run ? report.total_found - report.total_skipped : report.total_imported
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-3 gap-3">
        <Stat label="Reservas encontradas" value={report.total_found} />
        <Stat label={report.dry_run ? 'Seriam importadas' : 'Importadas'} value={toImport} tone="success" />
        <Stat label="Puladas" value={report.total_skipped} tone={report.total_skipped > 0 ? 'warning' : undefined} />
      </dl>

      {report.unknown_rooms.length > 0 && (
        <div className="rounded-lg border border-warning/30 bg-warning-tint px-4 py-3 text-[13px] text-warning">
          <p className="flex items-center gap-2 font-semibold">
            <AlertTriangle className="size-4" aria-hidden="true" /> Salas da planilha que não existem no cadastro
          </p>
          <p className="mt-0.5 text-[12.5px]">
            Cadastre essas salas (ou ajuste os nomes na planilha) e simule de novo.
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {report.unknown_rooms.map((name) => (
              <span
                key={name}
                className="rounded-[4px] border border-warning/30 bg-surface px-1.5 py-px font-mono text-[12px] text-ink-2"
              >
                {name}
              </span>
            ))}
          </div>
        </div>
      )}

      {report.errors.length > 0 && (
        <div
          role="alert"
          className="rounded-lg border border-danger/25 bg-danger-tint px-4 py-3 text-[13px] text-danger-ink"
        >
          <p className="font-semibold">Erros</p>
          <ul className="mt-1 list-inside list-disc space-y-0.5 text-[12.5px]">
            {report.errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}

      <DataTable>
        <thead>
          <tr>
            <Th>Aba</Th>
            <Th>Data</Th>
            <Th>Situação</Th>
            <Th className="text-right">Encontradas</Th>
            <Th className="text-right">{report.dry_run ? 'A importar' : 'Importadas'}</Th>
            <Th className="text-right">Puladas</Th>
            <Th>Avisos</Th>
          </tr>
        </thead>
        <tbody>
          {report.sheets.map((sheet, i) => (
            <SheetRow key={`${sheet.sheet_name}-${i}`} sheet={sheet} dryRun={report.dry_run} />
          ))}
          {report.sheets.length === 0 && (
            <tr>
              <td colSpan={7} className="px-3 py-6 text-center text-muted">
                Nenhuma aba encontrada na planilha.
              </td>
            </tr>
          )}
        </tbody>
      </DataTable>
    </div>
  )
}

function SheetRow({ sheet, dryRun }: { sheet: ImportSheetReport; dryRun: boolean }) {
  const [open, setOpen] = useState(false)
  const imported = dryRun ? sheet.bookings_found - sheet.bookings_skipped : sheet.bookings_imported
  return (
    <>
      <tr className="hover:bg-paper/60">
        <Td className="font-medium">{sheet.sheet_name}</Td>
        <Td className="font-mono text-[12.5px]">{sheet.date ? formatTabLabel(sheet.date) : '—'}</Td>
        <Td>
          <Badge tone={STATUS_TONES[sheet.status]}>{IMPORT_STATUS_LABELS[sheet.status]}</Badge>
        </Td>
        <Td className="tabular text-right font-mono">{sheet.bookings_found}</Td>
        <Td className="tabular text-right font-mono">{Math.max(0, imported)}</Td>
        <Td className="tabular text-right font-mono">{sheet.bookings_skipped}</Td>
        <Td>
          {sheet.warnings.length === 0 ? (
            <span className="text-faint">—</span>
          ) : (
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              className="flex items-center gap-1 text-[12.5px] font-medium text-warning hover:underline"
            >
              <ChevronRight className={cn('size-3.5 transition-transform', open && 'rotate-90')} aria-hidden="true" />
              {sheet.warnings.length} aviso{sheet.warnings.length === 1 ? '' : 's'}
            </button>
          )}
        </Td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7} className="border-b border-line bg-warning-tint/40 px-6 py-2">
            <ul className="list-inside list-disc space-y-0.5 text-[12.5px] text-ink-2">
              {sheet.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </td>
        </tr>
      )}
    </>
  )
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: 'success' | 'warning' }) {
  return (
    <div className="rounded-lg border border-line-strong bg-surface px-4 py-3">
      <dt className="text-[12px] text-muted">{label}</dt>
      <dd
        className={cn(
          'tabular mt-0.5 font-mono text-[24px] leading-tight font-medium',
          tone === 'success' ? 'text-success' : tone === 'warning' ? 'text-warning' : 'text-ink',
        )}
      >
        {value}
      </dd>
    </div>
  )
}
