/**
 * Utilitaires d'annotation du texte côté client.
 * Associe chaque correction à une position dans le texte brut.
 */
import type { Correction } from './types'

export interface TextSegment {
  text: string
  correctionId: string | null
}

export interface AnnotationResult {
  segments: TextSegment[]
  /** Map correctionId → {start, end} dans le texte original */
  positions: Map<string, { start: number; end: number }>
}

export function annotateText(text: string, corrections: Correction[]): AnnotationResult {
  const rawPositions: { start: number; end: number; id: string }[] = []
  const positions = new Map<string, { start: number; end: number }>()

  for (const correction of corrections) {
    const pos = findPosition(text, correction.snippet, correction.context)
    if (pos) {
      rawPositions.push({ ...pos, id: correction.id })
      positions.set(correction.id, pos)
    }
  }

  // Trier par position de début
  rawPositions.sort((a, b) => a.start - b.start)

  // Supprimer les chevauchements (garder le premier)
  const clean: typeof rawPositions = []
  for (const pos of rawPositions) {
    if (clean.length === 0 || pos.start >= clean[clean.length - 1].end) {
      clean.push(pos)
    }
  }

  // Construire les segments
  const segments: TextSegment[] = []
  let cursor = 0

  for (const pos of clean) {
    if (cursor < pos.start) {
      segments.push({ text: text.slice(cursor, pos.start), correctionId: null })
    }
    segments.push({ text: text.slice(pos.start, pos.end), correctionId: pos.id })
    cursor = pos.end
  }

  if (cursor < text.length) {
    segments.push({ text: text.slice(cursor), correctionId: null })
  }

  return { segments, positions }
}

/**
 * Trouve la position d'un snippet dans le texte.
 * Utilise le contexte pour lever les ambiguïtés si le snippet apparaît plusieurs fois.
 */
function findPosition(
  text: string,
  snippet: string,
  context: string
): { start: number; end: number } | null {
  if (!snippet) return null

  // Chercher d'abord dans la zone du contexte
  const ctxKey = context.slice(0, Math.min(50, context.length)).trim()
  if (ctxKey) {
    const ctxIdx = text.indexOf(ctxKey)
    if (ctxIdx !== -1) {
      // Chercher le snippet dans une fenêtre autour du contexte
      const searchFrom = Math.max(0, ctxIdx - 20)
      const searchTo = Math.min(text.length, ctxIdx + context.length + 20)
      const idx = text.indexOf(snippet, searchFrom)
      if (idx !== -1 && idx < searchTo) {
        return { start: idx, end: idx + snippet.length }
      }
    }
  }

  // Fallback : première occurrence dans tout le texte
  const idx = text.indexOf(snippet)
  if (idx !== -1) {
    return { start: idx, end: idx + snippet.length }
  }

  // Dernier recours : recherche insensible à la casse
  const lowerText = text.toLowerCase()
  const lowerSnippet = snippet.toLowerCase()
  const lowerIdx = lowerText.indexOf(lowerSnippet)
  if (lowerIdx !== -1) {
    return { start: lowerIdx, end: lowerIdx + snippet.length }
  }

  return null
}
