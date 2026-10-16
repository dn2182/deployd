import { useId, useState } from 'react'
import { useLanguage, useT } from '../i18n/index.js'
import { HELP } from '../help/content.js'
import { Modal } from './ConfirmDialog.jsx'
import { Button, Help, inputClass } from './ui.jsx'

export default function HelpDialog({ onClose }) {
  const t = useT()
  const { language } = useLanguage()
  const id = useId()
  const [query, setQuery] = useState('')
  const entries = HELP[language] ?? HELP.en
  const needle = query.trim().toLowerCase()
  const visible = needle
    ? entries.filter((entry) => `${entry.q} ${entry.a}`.toLowerCase().includes(needle))
    : entries

  return (
    <Modal labelledBy={`${id}-title`} onClose={onClose} className="w-[min(44rem,calc(100vw-2rem))]">
      <div className="flex flex-col gap-4 p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id={`${id}-title`} className="m-0 text-lg font-semibold tracking-tight text-text-strong">{t('help.title')}</h2>
            <Help className="mt-1">{t('help.intro')}</Help>
          </div>
          <Button size="small" type="button" onClick={onClose}>{t('help.close')}</Button>
        </div>
        <input
          className={inputClass}
          type="search"
          value={query}
          placeholder={t('help.search')}
          aria-label={t('help.search')}
          autoFocus
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="flex max-h-[60vh] flex-col gap-1 overflow-y-auto pr-1">
          {visible.length === 0 && <Help>{t('help.no_matches')}</Help>}
          {visible.map((entry) => (
            <details key={entry.q} open={Boolean(needle)} className="group rounded-card border border-border-subtle bg-surface-soft px-3 py-2">
              <summary className="cursor-pointer list-none text-sm font-semibold text-text-strong marker:hidden">
                <span className="mr-2 inline-block text-muted transition group-open:rotate-90">&rsaquo;</span>{entry.q}
              </summary>
              <p className="mt-2 mb-1 text-xs leading-relaxed text-text">{entry.a}</p>
            </details>
          ))}
        </div>
      </div>
    </Modal>
  )
}
