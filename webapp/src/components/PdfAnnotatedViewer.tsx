'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
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

/** Normalise les espaces pour une comparaison souple */
function normalize(s: string): string {
  return s.replace(/\s+/g, ' ').trim().toLowerCase()
}

/** Construit le renderer de texte pour une page donnée */
function buildTextRenderer(
  pageCorrections: Correction[],
  selectedId: string | null,
  onSelect: (id: string) => void
) {
  return ({ str }: { str: string; itemIndex: number }) => {
    if (!str.trim() || pageCorrections.length === 0) return str

    const normStr = normalize(str)
    const matches: Array<{ start: number; end: number; correction: Correction }> = []

    for (const correction of pageCorrections) {
      const normSnip = normalize(correction.snippet)
      if (!normSnip) continue

      // Cherche le snippet normalisé dans le texte normalisé
      let searchFrom = 0
      const idx = normStr.indexOf(normSnip, searchFrom)
      if (idx === -1) continue

      // Mapper l'indice normalisé → indice dans str original (approximatif)
      // On utilise le ratio des longueurs
      const ratio = str.length / normStr.length
      const approxStart = Math.round(idx * ratio)
      const approxEnd = Math.round((idx + normSnip.length) * ratio)

      // Vérification par substring pour éviter les faux positifs
      const candidate = str.slice(approxStart, approxEnd)
      const normCandidate = normalize(candidate)
      if (normCandidate.length === 0) continue

      matches.push({
        start: approxStart,
        end: approxEnd,
        correction,
      })
      searchFrom = approxEnd
    }

    if (matches.length === 0) return str

    // Trier par position, construire le HTML
    matches.sort((a, b) => a.start - b.start)

    let html = ''
    let pos = 0
    for (const { start, end, correction } of matches) {
      if (start < pos) continue // chevauchement → ignorer
      html += escapeHtml(str.slice(pos, start))

      const isSelected = correction.id === selectedId
      const bg = isSelected
        ? CATEGORY_SELECTED_BG[correction.category] ?? CATEGORY_SELECTED_BG.style
        : CATEGORY_BG[correction.category] ?? CATEGORY_BG.style
      const outline = isSelected ? 'outline:2px solid #1e3a5f;outline-offset:1px;' : ''
      const borderBottom = `border-bottom:2px solid ${CATEGORY_DOT[correction.category] ?? '#888'};`
      const tooltip = escapeHtml(`${correction.rule} → ${correction.corrected}`)

      html += `<mark style="background:${bg};${borderBottom}${outline}border-radius:2px;padding:0 1px;cursor:pointer;" data-corr-id="${correction.id}" title="${tooltip}">${escapeHtml(str.slice(start, end))}</mark>`
      pos = end
    }
    html += escapeHtml(str.slice(pos))
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

  // Scroll vers une correction
  const scrollToCorrection = useCallback(
    (id: string) => {
      const corr = corrections.find((c) => c.id === id)
      if (!corr) return
      const page = corr.pageNum ?? 1
      const ref = pageRefs.current.get(page)
      ref?.scrollIntoView({ behavior: 'smooth', block: 'center' })
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

  // Adapter la largeur selon le container
  useEffect(() => {
    if (!containerRef.current) return
    const obs = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w) setPageWidth(Math.min(w - 48, 900))
    })
    obs.observe(containerRef.current)
    return () => obs.disconnect()
  }, [])

  // Renderer de texte mémoïsé par page
  const getTextRenderer = useCallback(
    (pageNum: number) =>
      buildTextRenderer(corrsByPage.get(pageNum) ?? [], selectedId, onSelect),
    [corrsByPage, selectedId, onSelect]
  )

  const totalActive = activeCorrections.length
  const totalDone = doneIds.size

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* ── Barre de navigation ─────────────────────────────────────────────── */}
      <div className="sticky top-0 z-10 bg-white border-b border-gray-200 px-4 py-2 flex items-center gap-3 flex-wrap shadow-sm">
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
        <div className="ml-auto flex items-center gap-2 text-xs text-gray-400 hidden md:flex">
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
          className="flex flex-col items-center gap-6"
        >
          {Array.from({ length: numPages }, (_, i) => i + 1).map((pageNum) => {
            const pageActive = activeCorrsByPage.get(pageNum) ?? 0
            const pageTotal = corrsByPage.get(pageNum)?.length ?? 0
            const pageDone = pageTotal - pageActive

            return (
              <div
                key={pageNum}
                ref={(el) => {
                  if (el) pageRefs.current.set(pageNum, el)
                  else pageRefs.current.delete(pageNum)
                }}
                className="relative"
              >
                {/* Indicateur de corrections dans la marge gauche */}
                {pageTotal > 0 && (
                  <div className="absolute -left-5 top-2 flex flex-col gap-0.5 items-end z-10">
                    {corrsByPage.get(pageNum)!.map((c) => (
                      <button
                        key={c.id}
                        onClick={(e) => {
                          e.stopPropagation()
                          goTo(c.id)
                        }}
                        title={`${c.rule} — ${c.snippet}`}
                        className={`w-2.5 h-2.5 rounded-full transition-all ${
                          doneIds.has(c.id) ? 'opacity-30' : 'opacity-90 hover:scale-125'
                        } ${c.id === selectedId ? 'ring-2 ring-offset-1 ring-gray-600 scale-125' : ''}`}
                        style={{ backgroundColor: CATEGORY_DOT[c.category] ?? '#888' }}
                      />
                    ))}
                  </div>
                )}

                {/* Page PDF */}
                <div className="shadow-lg rounded overflow-hidden bg-white">
                  <Page
                    pageNumber={pageNum}
                    width={pageWidth}
                    renderTextLayer={true}
                    renderAnnotationLayer={false}
                    customTextRenderer={getTextRenderer(pageNum)}
                  />
                </div>

                {/* Étiquette de page + compteur de corrections */}
                <div className="mt-1.5 flex items-center justify-between text-xs text-gray-400 px-1">
                  <span>page {pageNum}</span>
                  {pageTotal > 0 && (
                    <div className="flex items-center gap-1.5">
                      {/* Dots par catégorie sur cette page */}
                      {(['orthographe', 'grammaire', 'typographie', 'style'] as const).map(
                        (cat) => {
                          const n = corrsByPage
                            .get(pageNum)!
                            .filter((c) => c.category === cat && !doneIds.has(c.id)).length
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
                        }
                      )}
                      {pageDone > 0 && (
                        <span className="text-green-500">· {pageDone} faites</span>
                      )}
                    </div>
                  )}
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
        <div className="bg-white border-t border-gray-200 px-4 py-2 flex items-center gap-3 text-xs text-gray-500">
          <span>Progression :</span>
          <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden max-w-xs">
            <div
              className="h-full bg-green-400 rounded-full transition-all duration-300"
              style={{ width: `${Math.round((totalDone / corrections.length) * 100)}%` }}
            />
          </div>
          <span className="text-gray-400">
            {totalDone} / {corrections.length} ({Math.round((totalDone / corrections.length) * 100)}
            %)
          </span>
        </div>
      )}
    </div>
  )
}
