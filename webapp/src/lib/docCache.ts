/**
 * Cache local de l'analyse complète d'un document.
 * Clé = (nom + taille + date de modification du fichier).
 * Stocké dans localStorage — permet de restaurer une session sans
 * rappeler l'API Claude (économie de tokens).
 */
import type { AnalysisResult } from './types'

const DOC_PREFIX = 'editoria_doc_v1_'
const LAST_KEY = 'editoria_last_v1'

export interface CachedDoc {
  result: AnalysisResult
  doneIds: string[]
  selectedId: string | null
  savedAt: number
  filename: string
}

export interface LastDocMeta {
  cacheKey: string
  filename: string
  savedAt: number
  doneCount: number
  total: number
}

/** Clé stable basée sur les métadonnées du fichier (pas de lecture du contenu) */
export function fileCacheKey(file: File): string {
  const raw = `${file.name}|${file.size}|${file.lastModified}`
  // btoa safe (ASCII uniquement après encodage)
  return DOC_PREFIX + encodeURIComponent(raw)
}

export function loadCachedDoc(file: File): CachedDoc | null {
  try {
    const raw = localStorage.getItem(fileCacheKey(file))
    return raw ? (JSON.parse(raw) as CachedDoc) : null
  } catch {
    return null
  }
}

/** Sauvegarde complète après une nouvelle analyse */
export function saveCachedDoc(
  file: File,
  result: AnalysisResult,
  doneIds: string[],
  selectedId: string | null
): void {
  const doc: CachedDoc = { result, doneIds, selectedId, savedAt: Date.now(), filename: file.name }
  _write(fileCacheKey(file), doc)
  _writeMeta(file, doneIds.length, result.corrections.length)
}

/** Mise à jour légère : coches + sélection (ne ré-écrit pas l'analyse) */
export function updateCachedSession(
  file: File,
  doneIds: string[],
  selectedId: string | null
): void {
  try {
    const raw = localStorage.getItem(fileCacheKey(file))
    if (!raw) return
    const doc: CachedDoc = JSON.parse(raw)
    doc.doneIds = doneIds
    doc.selectedId = selectedId
    doc.savedAt = Date.now()
    localStorage.setItem(fileCacheKey(file), JSON.stringify(doc))
    _writeMeta(file, doneIds.length, doc.result.corrections.length)
  } catch {
    // ignore
  }
}

export function loadLastDocMeta(): LastDocMeta | null {
  try {
    const raw = localStorage.getItem(LAST_KEY)
    return raw ? (JSON.parse(raw) as LastDocMeta) : null
  } catch {
    return null
  }
}

// ── Helpers privés ────────────────────────────────────────────────────────────

function _write(key: string, doc: CachedDoc): void {
  try {
    localStorage.setItem(key, JSON.stringify(doc))
  } catch {
    // Quota dépassé → vider les anciens docs et réessayer
    _clearOld()
    try {
      localStorage.setItem(key, JSON.stringify(doc))
    } catch {
      // Abandon silencieux (doc trop volumineux)
    }
  }
}

function _writeMeta(file: File, doneCount: number, total: number): void {
  try {
    const meta: LastDocMeta = {
      cacheKey: fileCacheKey(file),
      filename: file.name,
      savedAt: Date.now(),
      doneCount,
      total,
    }
    localStorage.setItem(LAST_KEY, JSON.stringify(meta))
  } catch {
    // ignore
  }
}

function _clearOld(): void {
  try {
    Object.keys(localStorage)
      .filter((k) => k.startsWith(DOC_PREFIX))
      .forEach((k) => localStorage.removeItem(k))
  } catch {
    // ignore
  }
}
