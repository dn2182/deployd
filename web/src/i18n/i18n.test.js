import { afterEach, describe, expect, it, vi } from 'vitest'
import { detectLanguage, translate } from './index.js'
import en from './en.js'
import es from './es.js'

afterEach(() => vi.restoreAllMocks())

describe('i18n', () => {
  it('keeps both catalogs in sync', () => {
    expect(Object.keys(es).sort()).toEqual(Object.keys(en).sort())
  })

  it('contains no em-dashes and no opening inverted marks', () => {
    for (const catalog of [en, es]) {
      for (const value of Object.values(catalog)) {
        expect(value).not.toMatch(/[—¿¡]/)
      }
    }
  })

  it('warns in development on a missing key and returns the key', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(translate('en', 'nope.missing')).toBe('nope.missing')
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('nope.missing'))
  })

  it('falls back to English and interpolates values', () => {
    expect(translate('es', 'app.edit', { name: 'x' })).toBe('Editar x')
    expect(translate('fr', 'app.edit', { name: 'x' })).toBe('Edit x')
    expect(detectLanguage('es-CR')).toBe('es')
    expect(detectLanguage('en-GB')).toBe('en')
  })
})
