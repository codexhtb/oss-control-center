import { useSyncExternalStore } from 'react'

const storageKey = 'oss-simplified-effects'
const changeEvent = 'oss-effects-change'

function storedPreference(): boolean {
  try { return window.localStorage.getItem(storageKey) === 'true' }
  catch { return false }
}

let simplified = typeof window !== 'undefined' && storedPreference()

function applyPreference() {
  if (typeof document !== 'undefined') document.documentElement.dataset.effects = simplified ? 'simple' : 'full'
}

applyPreference()

export function getSimplifiedEffects(): boolean { return simplified }

export function setSimplifiedEffects(value: boolean): void {
  simplified = value
  try { window.localStorage.setItem(storageKey, String(value)) } catch { /* Keep the setting for this page. */ }
  applyPreference()
  window.dispatchEvent(new Event(changeEvent))
}

function subscribe(callback: () => void): () => void {
  const changed = () => callback()
  const fromAnotherTab = (event: StorageEvent) => {
    if (event.key === storageKey) { simplified = event.newValue === 'true'; applyPreference(); callback() }
  }
  window.addEventListener(changeEvent, changed)
  window.addEventListener('storage', fromAnotherTab)
  return () => { window.removeEventListener(changeEvent, changed); window.removeEventListener('storage', fromAnotherTab) }
}

export function useSimplifiedEffects(): boolean {
  return useSyncExternalStore(subscribe, getSimplifiedEffects, () => false)
}

function reducedMotion(): boolean {
  return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
}

function subscribeToReducedMotion(callback: () => void): () => void {
  const query = window.matchMedia?.('(prefers-reduced-motion: reduce)')
  query?.addEventListener?.('change', callback)
  return () => query?.removeEventListener?.('change', callback)
}

export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeToReducedMotion, reducedMotion, () => true)
}
