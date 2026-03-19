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

interface Props {
  text: string
  formattedHtml?: string
  corrections: Correction[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function AnnotatedText({ text, formattedHtml, corrections, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  // ── Mode HTML (DOCX avec mise en forme) ──────────────────────────────────
  const annotatedHtml = useMemo(() => {
    if (!formattedHtml) return null
    return injectHighlightsIntoHtml(formattedHtml, corrections)
  }, [formattedHtml, corrections])

  // Mettre à jour la sélection via manipulation DOM (sans re-render)
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

  // ── Mode texte brut (PDF ou DOCX sans HTML) ───────────────────────────────
  const { segments } = useMemo(() => annotateText(text, corrections), [text, corrections])
  const correctionMap = useMemo(
    () => new Map(corrections.map((c) => [c.id, c])),
    [corrections]
  )

  useEffect(() => {
    if (selectedId) {
      const el = document.getElementById(`ann-${selectedId}`)
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [selectedId])

  return (
    <div className="font-serif text-gray-900 leading-relaxed text-base whitespace-pre-wrap">
      {segments.map((seg, i) => {
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
