import { createContext, createElement, useCallback, useContext, useMemo } from 'react'
import en from './en.js'
import es from './es.js'

const messages = { en, es }

export const LANGUAGES = ['en', 'es']

export function detectLanguage(language = globalThis.navigator?.language) {
  return language?.toLowerCase().startsWith('es') ? 'es' : 'en'
}

export function translate(language, key, values = {}) {
  let template = messages[language]?.[key] ?? messages.en[key]
  if (template === undefined) {
    if (import.meta.env.DEV) console.warn(`i18n: missing key "${key}"`)
    template = key
  }
  return Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
    template,
  )
}

const LanguageContext = createContext({ language: 'en', setLanguage: () => {} })

export function LanguageProvider({ language, setLanguage, children }) {
  const value = useMemo(() => ({ language, setLanguage }), [language, setLanguage])
  return createElement(LanguageContext.Provider, { value }, children)
}

export function useLanguage() {
  return useContext(LanguageContext)
}

export function useT() {
  const { language } = useContext(LanguageContext)
  return useCallback((key, values) => translate(language, key, values), [language])
}
