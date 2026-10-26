import { type KeyboardEvent, useState } from 'react'
import { X } from 'lucide-react'

import { cn } from '../../lib/cn'

/** Lista de textos editável como etiquetas (Enter ou vírgula adiciona; Backspace remove a última). */
export function TagInput({
  id,
  value,
  onChange,
  placeholder,
  suggestions = [],
  'aria-describedby': describedBy,
}: {
  id?: string
  value: string[]
  onChange: (tags: string[]) => void
  placeholder?: string
  suggestions?: string[]
  'aria-describedby'?: string
}) {
  const [draft, setDraft] = useState('')

  const add = (raw: string) => {
    const tag = raw.trim().replace(/\s+/g, ' ')
    if (!tag) return
    if (!value.some((t) => t.toLocaleLowerCase('pt-BR') === tag.toLocaleLowerCase('pt-BR'))) onChange([...value, tag])
    setDraft('')
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' || event.key === ',') {
      if (draft.trim()) {
        event.preventDefault()
        add(draft)
      }
    } else if (event.key === 'Backspace' && !draft && value.length > 0) {
      onChange(value.slice(0, -1))
    }
  }

  const remaining = suggestions.filter((s) => !value.includes(s))

  return (
    <div>
      <div
        className={cn(
          'flex min-h-9 w-full flex-wrap items-center gap-1 rounded-md border border-line-strong bg-surface px-1.5 py-1',
          'focus-within:border-video focus-within:ring-3 focus-within:ring-video/15',
        )}
      >
        {value.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 rounded-[4px] border border-line-strong bg-paper py-px pr-0.5 pl-1.5 text-[12px] text-ink-2"
          >
            {tag}
            <button
              type="button"
              aria-label={`Remover ${tag}`}
              onClick={() => onChange(value.filter((t) => t !== tag))}
              className="rounded p-px text-muted hover:bg-ink/[0.08] hover:text-ink"
            >
              <X className="size-3" />
            </button>
          </span>
        ))}
        <input
          id={id}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onBlur={() => add(draft)}
          placeholder={value.length === 0 ? placeholder : undefined}
          aria-describedby={describedBy}
          className="h-6 min-w-[120px] flex-1 bg-transparent px-1 text-[13px] outline-none placeholder:text-faint"
        />
      </div>
      {remaining.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {remaining.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => add(s)}
              className="rounded-[4px] border border-dashed border-line-strong px-1.5 py-px text-[11.5px] text-muted hover:border-muted hover:text-ink"
            >
              + {s}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
