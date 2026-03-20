'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import type { TextContent, TextItem } from 'react-pdf'
import 'react-pdf/dist/Page/TextLayer.css'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import type { Correction } from '@/lib/types'

// Configurer le worker PDF.js via CDN (évite les problèmes de bundling Next.js)
pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`

// ── Couleurs par catégorie ─────────────────────────────────────────────────

const CATEGORY_BG: Record<string, string> = {
  orthographe: 'rgba(239,68,68,0.25)',
  grammaire: 'rgba(249,115,22,0.25)',
  typographie: 'rgba(59,130,246,0.25)',
  style: 'rgba(34,197,94,0.25)',
}

const CATEGORY_SELECTED_BG: Record<string, string> = {
  orthographe: 'rgba(239,68,68,0.55)',
  grammaire: 'rgba(249,115,22,0.55)',
  typographie: 'rgba(59,130,246,0.55)',
  style: 'rgba(34,197,94,0.55)',
}

const CATEGORY_DOT: Record<string, string> = {
  orthographe: '#ef4444',
  grammaire: '#f97316',
  typographie: '#3b82f6',
  style: '#22c55e',
}

const CATEGORY_LABEL: Record<string, string> = {
  orthographe: 'Orth.',
  grammaire: 'Gram.',
  typographie: 'Typo.',
  style: 'Style',
}

// ── Helpers ────────────────────────────────────────────────────────────────

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/**
 * Trouve les positions [start, end] du snippet dans str.
 * 1. Essai direct insensible à la casse
 * 2. Repli regex avec espaces flexibles
 */
function findSnippet(str: string, snippet: string): [number, number] | null {
  if (!snippet.trim()) return null

  const lowerStr = str.toLowerCase()
  const lowerSnip = snippet.toLowerCase()
  const directIdx = lowerStr.indexOf(lowerSnip)
  if (directIdx !== -1) return [directIdx, directIdx + snippet.length]

  try {
    const escaped = snippet
      .trim()
      .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      .replace(/\s+/g, '\\s+')
    const regex = new RegExp(escaped, 'i')
    const m = str.match(regex)
    if (m && m.index !== undefined) return [m.index, m.index + m[0].length]
  } catch {
    // regex invalide → on abandonne
  }

  return null
}

// ── Données de texte pré-calculées par page ───────────────────────────────

interface ItemRange {
  start: number
  end: number
}

interface PageTextData {
  /** Position de chaque item (index → {start, end}) dans le texte complet */
  itemRanges: ItemRange[]
  /** Texte complet de la page (concaténation de tous les items) */
  fullText: string
  /** Position du snippet dans le texte complet, par correctionId */
  snippetPositions: Map<string, [number, number] | null>
}

// ── Renderer de texte utilisant les données pré-calculées ─────────────────

function buildTextRenderer(
  pageTextData: PageTextData | undefined,
  pageCorrections: Correction[],
  selectedId: string | null,
) {
  return ({ str, itemIndex }: { str: string; itemIndex: number }) => {
    if (!str.trim() || pageCorrections.length === 0) return str

    const matches: Array<{ start: number; end: number; correction: Correction }> = []

    if (pageTextData) {
      // ── Correspondance multi-items via le texte complet de la page ──────
      const itemRange = pageTextData.itemRanges[itemIndex]
      if (itemRange) {
        for (const corr of pageCorrections) {
          const pos = pageTextData.snippetPositions.get(corr.id)
          if (!pos) continue
          const [snipStart, snipEnd] = pos

          // Ignorer si le snippet ne chevauche pas cet item
          if (snipEnd <= itemRange.start || snipStart >= itemRange.end) continue

          // Calculer l'overlap dans le référentiel de l'item
          const start = Math.max(snipStart, itemRange.start) - itemRange.start
          const end = Math.min(snipEnd, itemRange.end) - itemRange.start
          matches.push({ start, end, correction: corr })
        }
      }
    } else {
      // ── Fallback : correspondance directe dans l'item (snippets courts) ──
      for (const corr of pageCorrections) {
        const pos = findSnippet(str, corr.snippet)
        if (pos) matches.push({ start: pos[0], end: pos[1], correction: corr })
      }
    }

    if (matches.length === 0) return str

    matches.sort((a, b) => a.start - b.start)

    let html = ''
    let cursor = 0
    for (const { start, end, correction } of matches) {
      if (start < cursor) continue
      html += escapeHtml(str.slice(cursor, start))

      const isSelected = correction.id === selectedId
      const bg = isSelected
        ? (CATEGORY_SELECTED_BG[correction.category] ?? CATEGORY_SELECTED_BG.style)
        : (CATEGORY_BG[correction.category] ?? CATEGORY_BG.style)
      const outline = isSelected ? 'outline:2px solid #1e3a5f;outline-offset:1px;' : ''
      const borderBottom = `border-bottom:2px solid ${CATEGORY_DOT[correction.category] ?? '#888'};`
      const tooltip = escapeHtml(`${correction.rule} → ${correction.corrected}`)

      html += `<mark style="background:${bg};${borderBottom}${outline}border-radius:2px;padding:0 1px;cursor:pointer;" data-corr-id="${correction.id}" title="${tooltip}">${escapeHtml(str.slice(start, end))}</mark>`
      cursor = end
    }
    html += escapeHtml(str.slice(cursor))
    return html
  }
}

// ── Composant principal ────────────────────────────────────────────────────

interface Props {
  pdfUrl: string
  corrections: Correction[]
  selectedId: string | null
  doneIds: Set<string>
  onSelect: (id: string) => void
  onToggleDone: (id: string) => void
}

export function PdfAnnotatedViewer({
  pdfUrl,
  corrections,
  selectedId,
  doneIds,
  onSelect,
  onToggleDone,
}: Props) {
  const [numPages, setNumPages] = useState(0)
  const [pageWidth, setPageWidth] = useState(700)
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const containerRef = useRef<HTMLDivElement>(null)

  // Données de texte pré-calculées par page (peuplées via onGetTextSuccess)
  const pageTextsRef = useRef<Map<number, PageTextData>>(new Map())
  // Compteur pour forcer le re-render du text layer quand une page charge
  const [textLoadCount, setTextLoadCount] = useState(0)

  // Corrections actives (non faites)
  const activeCorrections = useMemo(
    () => corrections.filter((c) => !doneIds.has(c.id)),
    [corrections, doneIds]
  )

  // Regrouper les corrections par page
  const corrsByPage = useMemo(() => {
    const map = new Map<number, Correction[]>()
    for (const c of corrections) {
      const p = c.pageNum ?? 1
      if (!map.has(p)) map.set(p, [])
      map.get(p)!.push(c)
    }
    return map
  }, [corrections])

  // Statistiques par page (non faites)
  const activeCorrsByPage = useMemo(() => {
    const map = new Map<number, number>()
    for (const c of activeCorrections) {
      const p = c.pageNum ?? 1
      map.set(p, (map.get(p) ?? 0) + 1)
    }
    return map
  }, [activeCorrections])

  // Ordre des corrections pour navigation
  const orderedIds = useMemo(() => corrections.map((c) => c.id), [corrections])
  const selectedIdx = selectedId ? orderedIds.indexOf(selectedId) : -1

  const prevId = selectedIdx > 0 ? orderedIds[selectedIdx - 1] : null
  const nextId = selectedIdx < orderedIds.length - 1 ? orderedIds[selectedIdx + 1] : null
  const firstActiveId = activeCorrections[0]?.id ?? null

  // Réinitialiser les données de texte quand le PDF change
  useEffect(() => {
    pageTextsRef.current.clear()
    setTextLoadCount(0)
  }, [pdfUrl])

  // ── Pré-calcul des positions de snippets via onGetTextSuccess ────────────
  const handleGetTextSuccess = useCallback(
    (pageNum: number, textContent: TextContent) => {
      // Filtrer pour ne garder que les TextItem (pas les TextMarkedContent)
      const items = textContent.items.filter(
        (item): item is TextItem => 'str' in item
      )

      // Construire le texte complet de la page et l'index de position de chaque item
      const itemRanges: ItemRange[] = []
      let offset = 0
      let fullText = ''

      for (const item of items) {
        itemRanges.push({ start: offset, end: offset + item.str.length })
        fullText += item.str
        offset += item.str.length
      }

      // Pré-calculer la position de chaque correction dans le texte complet
      const snippetPositions = new Map<string, [number, number] | null>()
      const pageCorrs = corrsByPage.get(pageNum) ?? []

      for (const corr of pageCorrs) {
        snippetPositions.set(corr.id, findSnippet(fullText, corr.snippet))
      }

      pageTextsRef.current.set(pageNum, { itemRanges, fullText, snippetPositions })

      // Déclencher un re-render pour que le text layer utilise les nouvelles données
      setTextLoadCount((n) => n + 1)
    },
    [corrsByPage]
  )

  // ── Renderer de texte mémoïsé par page ───────────────────────────────────
  const getTextRenderer = useCallback(
    (pageNum: number) =>
      buildTextRenderer(
        pageTextsRef.current.get(pageNum),
        corrsByPage.get(pageNum) ?? [],
        selectedId,
      ),
    // textLoadCount force la recréation quand onGetTextSuccess a fini de charger une page
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [corrsByPage, selectedId, textLoadCount]
  )

  // Scroll vers la correction — d'abord le <mark> dans le PDF, sinon la page
  const scrollToCorrection = useCallback(
    (id: string) => {
      const mark = containerRef.current?.querySelector(`[data-corr-id="${id}"]`)
      if (mark) {
        mark.scrollIntoView({ behavior: 'smooth', block: 'center' })
        return
      }
      const corr = corrections.find((c) => c.id === id)
      if (!corr) return
      pageRefs.current.get(corr.pageNum ?? 1)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    },
    [corrections]
  )

  const goTo = useCallback(
    (id: string) => {
      onSelect(id)
      scrollToCorrection(id)
    },
    [onSelect, scrollToCorrection]
  )

  // Auto-scroll quand la sélection change
  useEffect(() => {
    if (selectedId) scrollToCorrection(selectedId)
  }, [selectedId, scrollToCorrection])

  // Raccourcis clavier
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'ArrowLeft' && prevId) {
        e.preventDefault()
        goTo(prevId)
      } else if (e.key === 'ArrowRight' && nextId) {
        e.preventDefault()
        goTo(nextId)
      } else if (e.key === ' ' && selectedId) {
        e.preventDefault()
        onToggleDone(selectedId)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [prevId, nextId, selectedId, goTo, onToggleDone])

  // Clics sur les annotations dans le canvas PDF
  const handleContainerClick = useCallback(
    (e: React.MouseEvent) => {
      const target = e.target as HTMLElement
      const mark = target.closest('[data-corr-id]')
      if (mark) {
        const id = mark.getAttribute('data-corr-id')
        if (id) goTo(id)
      }
    },
    [goTo]
  )

  // Adapter la largeur selon le container (réserver ~256px pour le panneau corrections)
  useEffect(() => {
    if (!containerRef.current) return
    const obs = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w) setPageWidth(Math.min(w - 256, 760))
    })
    obs.observe(containerRef.current)
    return () => obs.disconnect()
  }, [])

  const totalDone = doneIds.size

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* ── Barre de navigation ─────────────────────────────────────────────── */}
      <div className="flex-shrink-0 bg-white border-b border-gray-200 px-4 py-2 flex items-center gap-3 flex-wrap shadow-sm">
        {/* Prev / Next correction */}
        <button
          onClick={() => prevId && goTo(prevId)}
          disabled={!prevId}
          title="Correction précédente (←)"
          className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          ← Précédente
        </button>

        <div className="flex items-center gap-2 text-xs text-gray-500">
          {selectedIdx >= 0 ? (
            <span className="font-medium text-gray-700">
              {selectedIdx + 1} / {corrections.length}
            </span>
          ) : (
            <span>{corrections.length} corrections</span>
          )}
          {totalDone > 0 && (
            <span className="text-green-600 font-medium">· {totalDone} faites</span>
          )}
        </div>

        <button
          onClick={() => nextId && goTo(nextId)}
          disabled={!nextId}
          title="Correction suivante (→)"
          className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          Suivante →
        </button>

        {/* Sauter à la 1re correction active */}
        {firstActiveId && firstActiveId !== selectedId && (
          <button
            onClick={() => goTo(firstActiveId)}
            className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-blue-50 border border-blue-200 text-blue-700 hover:bg-blue-100 transition-colors ml-1"
          >
            ⚡ 1ère non faite
          </button>
        )}

        {/* Marquer la correction sélectionnée */}
        {selectedId && (
          <button
            onClick={() => onToggleDone(selectedId)}
            title="Marquer comme faite (Espace)"
            className={`ml-1 px-3 py-1.5 text-xs rounded-lg border transition-colors ${
              doneIds.has(selectedId)
                ? 'border-green-300 bg-green-50 text-green-700 hover:bg-green-100'
                : 'border-gray-200 text-gray-600 hover:bg-gray-50'
            }`}
          >
            {doneIds.has(selectedId) ? '✓ Faite' : 'Marquer faite'}
          </button>
        )}

        {/* Légende raccourcis */}
        <div className="ml-auto hidden md:flex items-center gap-2 text-xs text-gray-400">
          <span>← → navigation</span>
          <span>·</span>
          <span>Espace : marquer faite</span>
        </div>
      </div>

      {/* ── Document PDF ────────────────────────────────────────────────────── */}
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto px-6 py-4"
        onClick={handleContainerClick}
      >
        <Document
          file={pdfUrl}
          onLoadSuccess={({ numPages: n }) => setNumPages(n)}
          loading={
            <div className="flex items-center justify-center py-16 text-gray-400 text-sm">
              Chargement du PDF…
            </div>
          }
          error={
            <div className="flex items-center justify-center py-16 text-red-500 text-sm">
              Impossible de charger le PDF.
            </div>
          }
          className="flex flex-col items-start gap-8"
        >
          {Array.from({ length: numPages }, (_, i) => i + 1).map((pageNum) => {
            const pageCorrs = corrsByPage.get(pageNum) ?? []
            const pageActive = activeCorrsByPage.get(pageNum) ?? 0
            const pageDone = pageCorrs.length - pageActive

            return (
              <div
                key={pageNum}
                ref={(el) => {
                  if (el) pageRefs.current.set(pageNum, el)
                  else pageRefs.current.delete(pageNum)
                }}
                className="flex items-start gap-4"
              >
                {/* ── Page PDF ── */}
                <div className="flex-shrink-0">
                  <div className="shadow-lg rounded overflow-hidden bg-white">
                    <Page
                      pageNumber={pageNum}
                      width={pageWidth}
                      renderTextLayer={true}
                      renderAnnotationLayer={false}
                      customTextRenderer={getTextRenderer(pageNum)}
                      onGetTextSuccess={(textContent) =>
                        handleGetTextSuccess(pageNum, textContent)
                      }
                    />
                  </div>
                  {/* Étiquette de page */}
                  <div className="mt-1.5 flex items-center gap-2 text-xs text-gray-400 px-1">
                    <span>page {pageNum}</span>
                    {pageCorrs.length > 0 && <span className="text-gray-300">·</span>}
                    {(['orthographe', 'grammaire', 'typographie', 'style'] as const).map((cat) => {
                      const n = pageCorrs.filter(
                        (c) => c.category === cat && !doneIds.has(c.id)
                      ).length
                      return n > 0 ? (
                        <span
                          key={cat}
                          className="flex items-center gap-0.5"
                          title={`${n} ${CATEGORY_LABEL[cat]}`}
                        >
                          <span
                            className="w-1.5 h-1.5 rounded-full inline-block"
                            style={{ backgroundColor: CATEGORY_DOT[cat] }}
                          />
                          <span>{n}</span>
                        </span>
                      ) : null
                    })}
                    {pageDone > 0 && (
                      <span className="text-green-500">· {pageDone} faites</span>
                    )}
                  </div>
                </div>

              </div>
            )
          })}
        </Document>

        {numPages === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-gray-400">
            <div className="text-3xl mb-3">📄</div>
            <p className="text-sm">Chargement en cours…</p>
          </div>
        )}
      </div>

      {/* ── Barre de progression globale ────────────────────────────────────── */}
      {corrections.length > 0 && (
        <div className="flex-shrink-0 bg-white border-t border-gray-200 px-4 py-2 flex items-center gap-3 text-xs text-gray-500">
          <span>Progression :</span>
          <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden max-w-xs">
            <div
              className="h-full bg-green-400 rounded-full transition-all duration-300"
              style={{ width: `${Math.round((totalDone / corrections.length) * 100)}%` }}
            />
          </div>
          <span className="text-gray-400">
            {totalDone} / {corrections.length} (
            {Math.round((totalDone / corrections.length) * 100)}%)
          </span>
        </div>
      )}
    </div>
  )
}
