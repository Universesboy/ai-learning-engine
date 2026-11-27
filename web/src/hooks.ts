import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const load = useCallback(async () => {
    if (!path) return
    setLoading(true)
    try { setData(await api<T>(path)); setError(null) } catch (e: any) { setError(e.message) } finally { setLoading(false) }
  }, [path])
  useEffect(() => { load() }, [load])
  return { data, error, loading, reload: load, setData }
}

export type ThemePref = 'system' | 'light' | 'dark'
export function useTheme(): [ThemePref, (t: ThemePref) => void] {
  const read = (): ThemePref => { try { return (localStorage.getItem('theme') as ThemePref) || 'system' } catch { return 'system' } }
  const [theme, setTheme] = useState<ThemePref>(read)
  useEffect(() => {
    const el = document.documentElement
    if (theme === 'system') delete el.dataset.theme; else el.dataset.theme = theme
    try { theme === 'system' ? localStorage.removeItem('theme') : localStorage.setItem('theme', theme) } catch { /* private mode */ }
  }, [theme])
  return [theme, setTheme]
}

/** true when the page is in dark mode (system or forced), re-evaluated when it changes */
export function useIsDark() {
  const get = () => {
    const forced = document.documentElement.dataset.theme
    return forced ? forced === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches
  }
  const [dark, setDark] = useState(get)
  useEffect(() => {
    const mq = matchMedia('(prefers-color-scheme: dark)')
    const update = () => setDark(get())
    const obs = new MutationObserver(update)
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    mq.addEventListener('change', update)
    return () => { obs.disconnect(); mq.removeEventListener('change', update) }
  }, [])
  return dark
}

export function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}
