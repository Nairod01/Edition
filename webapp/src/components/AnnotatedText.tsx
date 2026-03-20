'use client'

import { useMemo, useEffect, useRef } from 'react'
import { annotateText } from '@/lib/annotate'
import { injectHighlightsIntoHtml } from '@/lib/htmlAnnotate'
import type { Correction } from '@/lib/types'

const CATEGORY_STYLES: Record<string, { bg: string; border: string; hover: string }> = {
  orthographe: {
    bg: 'bg-red-100',
    border: 'border-b-2 border-red-400',
    hover: 'hover:bg-red-200',
  },
  grammaire: {
    bg: 'bg-orange-100',
    border: 'border-b-2 border-orange-400',
    hover: 'hover:bg-orange-200',
  },
  typographie: {
    bg: 'bg-blue-100',
    border: 'border-b-2 border-blue-400',
    hover: 'hover:bg-blue-200',
  },
  style: {
    bg: 'bg-green-100',
    border: 'border-b-2 border-green-400',
    hover: 'hover:bg-green-200',
  },
}

type EnrichedSegment =
  | { kind: 'text'; text: string; correctionId: string | null }
  | { kind: 'pageBreak'; pageNum: number }

/** Intercale des marqueurs de saut de page dans la liste de segments */
function buildSegmentsWithPageBreaks(
  baseSegments: { text: string; correctionId: string | null }[],
  pageOffsets: number[]
): EnrichedSegment[] {
  // pageOffsets[0] === 0 (début du doc), on insère un séparateur pour pages 2, 3, …
  const breaks = pageOffsets
    .slice(1)
    .map((off, i) => ({ off, pageNum: i + 2 }))
    .sort((a, b) => a.off - b.off)

  if (breaks.length === 0) {
    return baseSegments.map((s) => ({ kind: 'text', ...s }))
  }

  const result: EnrichedSegment[] = []
  let charPos = 0
  let breakIdx = 0

  for (const seg of baseSegments) {
    let text = seg.text
    let localStart = charPos

    // Consommer tous les sauts de page tombant dans ce segment
    while (breakIdx < breaks.length) {
      const { off, pageNum } = breaks[breakIdx]
      if (off >= localStart + text.length) break

      const cutAt = off - localStart
      if (cutAt > 0) {
        result.push({ kind: 'text', text: text.slice(0, cutAt), correctionId: seg.correctionId })
      }
      result.push({ kind: 'pageBreak', pageNum })
      text = text.slice(cutAt)
      localStart = off
      breakIdx++
    }

    if (text) result.push({ kind: 'text', text, correctionId: seg.correctionId })
    charPos += seg.text.length
  }

  return result
}

interface Props {
  text: string
  formattedHtml?: string
  pageOffsets?: number[]
  corrections: Correction[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function AnnotatedText({
  text,
  formattedHtml,
  pageOffsets,
  corrections,
  selectedId,
  onSelect,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  // ── Mode HTML (DOCX avec mise en forme) ──────────────────────────────────
  const annotatedHtml = useMemo(() => {
    if (!formattedHtml) return null
    return injectHighlightsIntoHtml(formattedHtml, corrections)
  }, [formattedHtml, corrections])

  useEffect(() => {
    if (!annotatedHtml || !containerRef.current) return
    containerRef.current
      .querySelectorAll('.correction-highlight')
      .forEach((el) => el.classList.remove('ring-2', 'ring-gray-800', 'ring-offset-1'))
    if (selectedId) {
      const el = containerRef.current.querySelector(`[data-id="${selectedId}"]`)
      if (el) {
        el.classList.add('ring-2', 'ring-gray-800', 'ring-offset-1')
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }
  }, [selectedId, annotatedHtml])

  if (annotatedHtml) {
    return (
      <div
        ref={containerRef}
        className="font-serif text-gray-900 leading-relaxed text-base formatted-doc"
        onClick={(e) => {
          const mark = (e.target as HTMLElement).closest('[data-id]')
          if (mark) {
            const id = mark.getAttribute('data-id')
            if (id) onSelect(id)
          }
        }}
        dangerouslySetInnerHTML={{ __html: annotatedHtml }}
      />
    )
  }

  // ── Mode texte brut (PDF) ─────────────────────────────────────────────────
  const { segments } = useMemo(() => annotateText(text, corrections), [text, corrections])
  const correctionMap = useMemo(
    () => new Map(corrections.map((c) => [c.id, c])),
    [corrections]
  )
  const enriched = useMemo(
    () => buildSegmentsWithPageBreaks(segments, pageOffsets ?? []),
    [segments, pageOffsets]
  )

  useEffect(() => {
    if (selectedId) {
      document.getElementById(`ann-${selectedId}`)?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
    }
  }, [selectedId])

  return (
    <div className="font-serif text-gray-900 leading-relaxed text-base whitespace-pre-wrap">
      {enriched.map((seg, i) => {
        if (seg.kind === 'pageBreak') {
          return (
            <div
              key={`pb-${i}`}
              className="flex items-center gap-3 my-6 select-none"
              aria-label={`Début page ${seg.pageNum}`}
            >
              <div className="flex-1 h-px bg-gray-200" />
              <span className="text-xs text-gray-400 font-medium px-2 py-0.5 rounded border border-gray-200 bg-white">
                page {seg.pageNum}
              </span>
              <div className="flex-1 h-px bg-gray-200" />
            </div>
          )
        }

        if (!seg.correctionId) {
          return <span key={i}>{seg.text}</span>
        }

        const correction = correctionMap.get(seg.correctionId)
        if (!correction) return <span key={i}>{seg.text}</span>

        const styles = CATEGORY_STYLES[correction.category] ?? CATEGORY_STYLES.style
        const isSelected = seg.correctionId === selectedId

        return (
          <span
            key={i}
            id={`ann-${seg.correctionId}`}
            className={`
              cursor-pointer rounded-sm px-0.5 transition-all duration-150
              ${styles.bg} ${styles.border} ${styles.hover}
              ${isSelected ? 'ring-2 ring-gray-800 ring-offset-1' : ''}
            `}
            title={`${correction.rule} → ${correction.corrected}`}
            onClick={() => onSelect(seg.correctionId!)}
          >
            {seg.text}
          </span>
        )
      })}
    </div>
  )
}
