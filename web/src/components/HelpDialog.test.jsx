import { fireEvent, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import HelpDialog from './HelpDialog.jsx'
import { HELP } from '../help/content.js'
import { renderWith } from '../test-utils.jsx'

describe('HelpDialog', () => {
  it('lists every question, filters by text, and closes', () => {
    const onClose = vi.fn()
    renderWith(<HelpDialog onClose={onClose} />)
    expect(screen.getByRole('dialog', { name: 'Help' })).toBeInTheDocument()
    expect(screen.getAllByRole('group')).toHaveLength(HELP.en.length)
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search questions' }), { target: { value: 'freeze' } })
    const groups = screen.getAllByRole('group')
    expect(groups).toHaveLength(1)
    expect(groups[0]).toHaveTextContent('What does Freeze do?')
    expect(groups[0]).toHaveAttribute('open')
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'zzz-no-such-thing' } })
    expect(screen.getByText('No questions match.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('uses the Spanish entries for Spanish', () => {
    renderWith(<HelpDialog onClose={() => {}} />, { language: 'es' })
    expect(screen.getByRole('dialog', { name: 'Ayuda' })).toBeInTheDocument()
    expect(screen.getAllByRole('group')).toHaveLength(HELP.es.length)
    expect(screen.getByText('Qué hace Congelar?')).toBeInTheDocument()
  })

  it('keeps both catalogs the same size with no opening marks or em-dashes', () => {
    expect(HELP.es).toHaveLength(HELP.en.length)
    for (const entry of [...HELP.en, ...HELP.es]) {
      expect(`${entry.q}${entry.a}`).not.toMatch(/[¿¡—]/)
    }
  })
})
