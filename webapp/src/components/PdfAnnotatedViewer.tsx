'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import type { TextContent, TextItem } from 'react-pdf'
import 'react-pdf/dist/Page/TextLayer.css'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import type { Correction, Category } from '@/lib/types'

pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`

// ── Couleurs annotations PDF ────────────────────────────────────────────────

const CATEGORY_BG: Record<string, string> = {
  orthographe: 'rgba(239,68,68,0.25)',
  grammaire: 'rgba(249,115,22,0.25)',
  typographie: 'rgba(59,130,246,0.25)',
  style: 'rgba(34,197,94,0.25)',
  coherence: 'rgba(168,85,247,0.25)',
  renvoi: 'rgba(234,179,8,0.25)',
}

const CATEGORY_SELECTED_BG: Record<string, string> = {
  orthographe: 'rgba(239,68,68,0.55)',
  grammaire: 'rgba(249,115,22,0.55)',
  typographie: 'rgba(59,130,246,0.55)',
  style: 'rgba(34,197,94,0.55)',
  coherence: 'rgba(168,85,247,0.55)',
  renvoi: 'rgba(234,179,8,0.55)',
}

const CATEGORY_DOT: Record<string, string> = {
  orthographe: '#ef4444',
  grammaire: '#f97316',
  typographie: '#3b82f6',
  style: '#22c55e',
  coherence: '#a855f7',
  renvoi: '#eab308',
}

const CATEGORY_LABEL_SHORT: Record<string, string> = {
  orthographe: 'Orth.',
  grammaire: 'Gram.',
  typographie: 'Typo.',
  style: 'Style',
  coherence: 'Cohér.',
  renvoi: 'Renvoi',
}

// ── Config panneau corrections ──────────────────────────────────────────────

const PANEL_CATEGORY_CONFIG: Record<
  Category,
  { label: string; dot: string; badge: string; border: string }
> = {
  orthographe: { label: 'Orthographe', dot: 'bg-red-500', badge: 'bg-red-50 text-red-700 border border-red-200', border: 'border-l-red-400' },
  grammaire: { label: 'Grammaire', dot: 'bg-orange-500', badge: 'bg-orange-50 text-orange-700 border border-orange-200', border: 'border-l-orange-400' },
  typographie: { label: 'Typographie', dot: 'bg-blue-500', badge: 'bg-blue-50 text-blue-700 border border-blue-200', border: 'border-l-blue-400' },
  style: { label: 'Style', dot: 'bg-green-500', badge: 'bg-green-50 text-green-700 border border-green-200', border: 'border-l-green-400' },
  coherence: { label: 'Cohérence', dot: 'bg-purple-500', badge: 'bg-purple-50 text-purple-700 border border-purple-200', border: 'border-l-purple-400' },
  renvoi: { label: 'Renvoi', dot: 'bg-yellow-500', badge: 'bg-yellow-50 text-yellow-700 border border-yellow-200', border: 'border-l-yellow-400' },
}

const SEVERITY_LABELS: Record<string, { label: string; color: string }> = {
  error: { label: 'Erreur', color: 'text-red-600' },
  warning: { label: 'Attention', color: 'text-orange-500' },
  suggestion: { label: 'Suggestion', color: 'text-blue-500' },
}

// ── Helpers ────────────────────────────────────────────────────────────────

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

function findSnippet(str: string, snippet: string): [number, number] | null {
  if (!snippet.trim()) return null
  const lowerStr = str.toLowerCase()
  const lowerSnip = snippet.toLowerCase()
  const directIdx = lowerStr.indexOf(lowerSnip)
  if (directIdx !== -1) return [directIdx, directIdx + snippet.length]
  try {
    const escaped = snippet.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+')
    const regex = new RegExp(escaped, 'i')
    const m = str.match(regex)
    if (m && m.index !== undefined) return [m.index, m.index + m[0].length]
  } catch { /* ignore */ }
  return null
}

interface ItemRange { start: number; end: number }

interface PageTextData {
  itemRanges: ItemRange[]
  fullText: string
  snippetPositions: Map<string, [number, number] | null>
}

function buildTextRenderer(
  pageTextData: PageTextData | undefined,
  pageCorrections: Correction[],
  selectedId: string | null,
) {
  return ({ str, itemIndex }: { str: string; itemIndex: number }) => {
    if (!str.trim() || pageCorrections.length === 0) return str

    const matches: Array<{ start: number; end: number; correction: Correction }> = []

    if (pageTextData) {
      const itemRange = pageTextData.itemRanges[itemIndex]
      if (itemRange) {
        for (const corr of pageCorrections) {
          const pos = pageTextData.snippetPositions.get(corr.id)
          if (!pos) continue
          const [snipStart, snipEnd] = pos
          if (snipEnd <= itemRange.start || snipStart >= itemRange.end) continue
          matches.push({
            start: Math.max(snipStart, itemRange.start) - itemRange.start,
            end: Math.min(snipEnd, itemRange.end) - itemRange.start,
            correction: corr,
          })
        }
      }
    } else {
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

// ── Carte correction ────────────────────────────────────────────────────────

function CorrectionCard({
  correction,
  isSelected,
  isDone,
  onSelect,
  onToggleDone,
}: {
  correction: Correction
  isSelected: boolean
  isDone: boolean
  onSelect: (id: string) => void
  onToggleDone: (id: string) => void
}) {
  const cfg = PANEL_CATEGORY_CONFIG[correction.category]
  const sev = SEVERITY_LABELS[correction.severity]
  return (
    <div
      id={`cp-${correction.id}`}
      className={`
        px-3 py-2.5 border-b border-gray-100 cursor-pointer border-l-4
        transition-colors duration-100
        ${cfg.border}
        ${isDone ? 'opacity-50' : ''}
        ${isSelected ? 'bg-blue-50' : 'bg-white hover:bg-gray-50'}
      `}
      onClick={() => onSelect(correction.id)}
    >
      <div className="flex items-center gap-2 mb-1 flex-wrap">
        <span className={`text-xs font-medium px-1.5 py-0.5 rounded-full ${cfg.badge}`}>
          {cfg.label}
        </span>
        <span className={`text-xs ${sev.color}`}>{sev.label}</span>
        <button
          className={`ml-auto flex items-center justify-center w-5 h-5 rounded-full border transition-colors duration-100 ${
            isDone
              ? 'bg-green-500 border-green-500 text-white'
              : 'border-gray-300 text-transparent hover:border-green-400 hover:text-green-400'
          }`}
          title={isDone ? 'Marquer comme non faite' : 'Marquer comme faite'}
          onClick={(e) => { e.stopPropagation(); onToggleDone(correction.id) }}
        >
          <CheckIcon className="w-3 h-3" />
        </button>
      </div>
      <div className={`flex items-center gap-1.5 mb-1 flex-wrap ${isDone ? 'line-through' : ''}`}>
        <span className="line-through text-red-500 text-xs font-mono bg-red-50 px-1 rounded">
          {correction.snippet}
        </span>
        <span className="text-gray-400 text-xs">→</span>
        <span className="text-green-700 font-semibold text-xs font-mono bg-green-50 px-1 rounded">
          {correction.corrected}
        </span>
      </div>
      <div className="text-xs font-semibold text-gray-800 mb-0.5">{correction.rule}</div>
      <div className="text-xs text-gray-500 leading-relaxed">{correction.explanation}</div>
    </div>
  )
}

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="2,6 5,9 10,3" />
    </svg>
  )
}

function FilterBtn({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`text-xs px-2 py-0.5 rounded-full font-medium transition-colors duration-100 ${
        active ? 'bg-gray-800 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
      }`}
    >
      {label}
    </button>
  )
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
  const [pageWidth, setPageWidth] = useState(600)
  const [filter, setFilter] = useState<Category | 'all'>('all')
  const [hideDone, setHideDone] = useState(false)

  // Refs pour layout
  const scrollRef = useRef<HTMLDivElement>(null)       // conteneur scroll unique
  const pdfColRef = useRef<HTMLDivElement>(null)        // colonne PDF (position:relative)
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map())

  // Texte PDF pré-calculé par page
  const pageTextsRef = useRef<Map<number, PageTextData>>(new Map())
  const [textLoadCount, setTextLoadCount] = useState(0)

  // Offsets Y de chaque page dans la colonne PDF + hauteur totale du doc
  const [pageTopOffsets, setPageTopOffsets] = useState<Map<number, number>>(new Map())
  const [docHeight, setDocHeight] = useState(0)

  const activeCorrections = useMemo(
    () => corrections.filter((c) => !doneIds.has(c.id)),
    [corrections, doneIds]
  )

  const corrsByPage = useMemo(() => {
    const map = new Map<number, Correction[]>()
    for (const c of corrections) {
      const p = c.pageNum ?? 1
      if (!map.has(p)) map.set(p, [])
      map.get(p)!.push(c)
    }
    return map
  }, [corrections])

  const activeCorrsByPage = useMemo(() => {
    const map = new Map<number, number>()
    for (const c of activeCorrections) {
      const p = c.pageNum ?? 1
      map.set(p, (map.get(p) ?? 0) + 1)
    }
    return map
  }, [activeCorrections])

  const counts = useMemo(() => ({
    orthographe: corrections.filter((c) => c.category === 'orthographe').length,
    grammaire: corrections.filter((c) => c.category === 'grammaire').length,
    typographie: corrections.filter((c) => c.category === 'typographie').length,
    style: corrections.filter((c) => c.category === 'style').length,
    coherence: corrections.filter((c) => c.category === 'coherence').length,
    renvoi: corrections.filter((c) => c.category === 'renvoi').length,
  }), [corrections])

  const orderedIds = useMemo(() => corrections.map((c) => c.id), [corrections])
  const selectedIdx = selectedId ? orderedIds.indexOf(selectedId) : -1
  const prevId = selectedIdx > 0 ? orderedIds[selectedIdx - 1] : null
  const nextId = selectedIdx < orderedIds.length - 1 ? orderedIds[selectedIdx + 1] : null
  const firstActiveId = activeCorrections[0]?.id ?? null
  const totalDone = doneIds.size
  const remaining = corrections.length - totalDone

  // ── Mesure des positions Y des pages ─────────────────────────────────────
  const measurePageOffsets = useCallback(() => {
    const container = pdfColRef.current
    if (!container || pageRefs.current.size === 0) return

    const newOffsets = new Map<number, number>()
    Array.from(pageRefs.current.entries()).forEach(([pageNum, el]) => {
      // Calcul du décalage depuis le haut de pdfColRef (qui est position:relative)
      let top = 0
      let curr: HTMLElement | null = el
      while (curr && curr !== container) {
        top += curr.offsetTop
        curr = curr.offsetParent as HTMLElement | null
      }
      newOffsets.set(pageNum, top)
    })
    setPageTopOffsets(new Map(newOffsets))
    setDocHeight(container.scrollHeight)
  }, [])

  useEffect(() => {
    pageTextsRef.current.clear()
    setTextLoadCount(0)
    setPageTopOffsets(new Map())
    setDocHeight(0)
  }, [pdfUrl])

  // Mesurer après chaque chargement de page ou redimensionnement
  useEffect(() => {
    if (numPages === 0) return
    const timer = setTimeout(measurePageOffsets, 150)
    return () => clearTimeout(timer)
  }, [numPages, pageWidth, textLoadCount, measurePageOffsets])

  // Observer les changements de taille de la colonne PDF
  useEffect(() => {
    if (!pdfColRef.current) return
    const obs = new ResizeObserver(measurePageOffsets)
    obs.observe(pdfColRef.current)
    return () => obs.disconnect()
  }, [measurePageOffsets])

  // ── Calcul largeur de page ────────────────────────────────────────────────
  // Réserver 320px pour le panneau corrections + padding
  useEffect(() => {
    if (!scrollRef.current) return
    const obs = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w) setPageWidth(Math.min(w - 320 - 48, 760))
    })
    obs.observe(scrollRef.current)
    return () => obs.disconnect()
  }, [])

  // ── Texte PDF ─────────────────────────────────────────────────────────────
  const handleGetTextSuccess = useCallback(
    (pageNum: number, textContent: TextContent) => {
      const items = textContent.items.filter((item): item is TextItem => 'str' in item)
      const itemRanges: ItemRange[] = []
      let offset = 0
      let fullText = ''
      for (const item of items) {
        itemRanges.push({ start: offset, end: offset + item.str.length })
        fullText += item.str
        offset += item.str.length
      }
      const snippetPositions = new Map<string, [number, number] | null>()
      for (const corr of corrsByPage.get(pageNum) ?? []) {
        snippetPositions.set(corr.id, findSnippet(fullText, corr.snippet))
      }
      pageTextsRef.current.set(pageNum, { itemRanges, fullText, snippetPositions })
      setTextLoadCount((n) => n + 1)
    },
    [corrsByPage]
  )

  const getTextRenderer = useCallback(
    (pageNum: number) =>
      buildTextRenderer(
        pageTextsRef.current.get(pageNum),
        corrsByPage.get(pageNum) ?? [],
        selectedId,
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [corrsByPage, selectedId, textLoadCount]
  )

  // ── Navigation ────────────────────────────────────────────────────────────
  const scrollToCorrection = useCallback(
    (id: string) => {
      const mark = scrollRef.current?.querySelector(`[data-corr-id="${id}"]`)
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
    (id: string) => { onSelect(id); scrollToCorrection(id) },
    [onSelect, scrollToCorrection]
  )

  useEffect(() => {
    if (selectedId) scrollToCorrection(selectedId)
  }, [selectedId, scrollToCorrection])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'ArrowLeft' && prevId) { e.preventDefault(); goTo(prevId) }
      else if (e.key === 'ArrowRight' && nextId) { e.preventDefault(); goTo(nextId) }
      else if (e.key === ' ' && selectedId) { e.preventDefault(); onToggleDone(selectedId) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [prevId, nextId, selectedId, goTo, onToggleDone])

  const handleContainerClick = useCallback(
    (e: React.MouseEvent) => {
      const mark = (e.target as HTMLElement).closest('[data-corr-id]')
      if (mark) {
        const id = mark.getAttribute('data-corr-id')
        if (id) goTo(id)
      }
    },
    [goTo]
  )

  // Pages numérotées triées ayant des corrections
  const pageNumsWithCorrs = useMemo(
    () => Array.from(corrsByPage.keys()).sort((a, b) => a - b),
    [corrsByPage]
  )

  return (
    <div className="flex flex-col h-full bg-gray-50">

      {/* ── Barre de navigation ─────────────────────────────────────────────── */}
      <div className="flex-shrink-0 bg-white border-b border-gray-200 px-4 py-2 flex items-center gap-3 flex-wrap shadow-sm">
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
            <span className="font-medium text-gray-700">{selectedIdx + 1} / {corrections.length}</span>
          ) : (
            <span>{corrections.length} corrections</span>
          )}
          {totalDone > 0 && <span className="text-green-600 font-medium">· {totalDone} faites</span>}
        </div>

        <button
          onClick={() => nextId && goTo(nextId)}
          disabled={!nextId}
          title="Correction suivante (→)"
          className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          Suivante →
        </button>

        {firstActiveId && firstActiveId !== selectedId && (
          <button
            onClick={() => goTo(firstActiveId)}
            className="flex items-center gap-1 px-3 py-1.5 text-xs rounded-lg bg-blue-50 border border-blue-200 text-blue-700 hover:bg-blue-100 transition-colors ml-1"
          >
            ⚡ 1ère non faite
          </button>
        )}

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

        <div className="ml-auto hidden md:flex items-center gap-2 text-xs text-gray-400">
          <span>← → navigation · Espace : marquer faite</span>
        </div>
      </div>

      {/* ── Barre de filtres ─────────────────────────────────────────────────── */}
      <div className="flex-shrink-0 bg-white border-b border-gray-200 px-4 py-2">
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-xs font-semibold text-gray-700">
            {corrections.length} correction{corrections.length > 1 ? 's' : ''}
          </span>
          {totalDone > 0 && (
            <span className="text-xs text-green-600 font-medium flex items-center gap-1">
              <CheckIcon className="w-3 h-3" />
              {totalDone} faite{totalDone > 1 ? 's' : ''}
              {remaining > 0 && (
                <span className="text-gray-400 font-normal"> · {remaining} restante{remaining > 1 ? 's' : ''}</span>
              )}
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-1">
          <FilterBtn label={`Tout (${corrections.length})`} active={filter === 'all'} onClick={() => setFilter('all')} />
          {(Object.entries(PANEL_CATEGORY_CONFIG) as [Category, typeof PANEL_CATEGORY_CONFIG[Category]][]).map(
            ([cat, cfg]) => counts[cat] > 0 ? (
              <FilterBtn key={cat} label={`${cfg.label} (${counts[cat]})`} active={filter === cat} onClick={() => setFilter(cat)} />
            ) : null
          )}
          {totalDone > 0 && (
            <FilterBtn
              label={hideDone ? `Afficher faites (${totalDone})` : 'Masquer faites'}
              active={hideDone}
              onClick={() => setHideDone((v) => !v)}
            />
          )}
        </div>
      </div>

      {/* ── Zone de scroll unique (PDF + panneau corrections) ────────────────── */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto" onClick={handleContainerClick}>
        <div className="flex">

          {/* ── Colonne PDF ── */}
          <div ref={pdfColRef} className="flex-shrink-0 relative py-4 px-4">
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
              className="flex flex-col gap-8"
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
                  >
                    <div className="shadow-lg rounded overflow-hidden bg-white">
                      <Page
                        pageNumber={pageNum}
                        width={pageWidth}
                        renderTextLayer={true}
                        renderAnnotationLayer={false}
                        customTextRenderer={getTextRenderer(pageNum)}
                        onGetTextSuccess={(tc) => handleGetTextSuccess(pageNum, tc)}
                      />
                    </div>
                    {/* Étiquette de page */}
                    <div className="mt-1.5 flex items-center gap-2 text-xs text-gray-400 px-1">
                      <span>page {pageNum}</span>
                      {pageCorrs.length > 0 && <span className="text-gray-300">·</span>}
                      {(['orthographe', 'grammaire', 'typographie', 'style', 'coherence', 'renvoi'] as const).map((cat) => {
                        const n = pageCorrs.filter((c) => c.category === cat && !doneIds.has(c.id)).length
                        return n > 0 ? (
                          <span key={cat} className="flex items-center gap-0.5" title={`${n} ${CATEGORY_LABEL_SHORT[cat]}`}>
                            <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ backgroundColor: CATEGORY_DOT[cat] }} />
                            <span>{n}</span>
                          </span>
                        ) : null
                      })}
                      {pageDone > 0 && <span className="text-green-500">· {pageDone} faites</span>}
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

          {/* ── Panneau corrections (même scroll, corrections alignées) ── */}
          <div
            className="w-80 flex-shrink-0 border-l border-gray-200 bg-white"
            style={{ position: 'relative', height: docHeight > 0 ? docHeight : '100%' }}
          >
            {docHeight > 0 && pageNumsWithCorrs.map((pageNum) => {
              const pageCorrs = corrsByPage.get(pageNum) ?? []
              const panelCorrs = pageCorrs
                .filter((c) => filter === 'all' || c.category === filter)
                .filter((c) => !hideDone || !doneIds.has(c.id))

              if (panelCorrs.length === 0) return null

              const top = pageTopOffsets.get(pageNum) ?? 0

              return (
                <div
                  key={pageNum}
                  style={{ position: 'absolute', top, width: '100%' }}
                >
                  {panelCorrs.map((correction) => (
                    <CorrectionCard
                      key={correction.id}
                      correction={correction}
                      isSelected={correction.id === selectedId}
                      isDone={doneIds.has(correction.id)}
                      onSelect={onSelect}
                      onToggleDone={onToggleDone}
                    />
                  ))}
                </div>
              )
            })}

            {/* Placeholder pendant le chargement */}
            {docHeight === 0 && numPages > 0 && (
              <div className="p-4 text-xs text-gray-400 italic">Chargement des corrections…</div>
            )}
          </div>

        </div>
      </div>

      {/* ── Barre de progression ─────────────────────────────────────────────── */}
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
            {totalDone} / {corrections.length} ({Math.round((totalDone / corrections.length) * 100)}%)
          </span>
        </div>
      )}
    </div>
  )
}
