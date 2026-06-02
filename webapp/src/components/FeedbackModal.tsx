'use client'

import { useEffect, useRef, useState } from 'react'
import type { Correction } from '@/lib/types'
import { FEEDBACK_REASONS, CATEGORY_META } from '@/lib/types'

interface Props {
  correction: Correction
  onConfirm: (reasonCodes: string[], comment: string | null) => Promise<void>
  onCancel: () => void
}

export function FeedbackModal({ correction, onConfirm, onCancel }: Props) {
  const [selectedReasons, setSelectedReasons] = useState<Set<string>>(new Set())
  const [otherText, setOtherText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const toggleReason = (code: string) => {
    setSelectedReasons(prev => {
      const next = new Set(prev)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  }

  // Focus sur la textarea quand "Autre" est sélectionné
  useEffect(() => {
    if (selectedReasons.has('other')) {
      setTimeout(() => textareaRef.current?.focus(), 50)
    }
  }, [selectedReasons])

  // Fermer avec Échap
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  async function handleSubmit() {
    setSubmitting(true)
    const codes = [...selectedReasons]
    const comment = selectedReasons.has('other') ? (otherText.trim() || null) : null
    await onConfirm(codes, comment)
    setSubmitting(false)
  }

  const meta = CATEGORY_META[correction.category]

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-[2px]"
        onClick={onCancel}
        aria-hidden="true"
      />

      {/* Panel */}
      <div className="relative w-full sm:max-w-md rounded-t-2xl sm:rounded-2xl bg-white shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">

        {/* Header */}
        <div className="px-5 py-4 border-b border-slate-100 flex items-start gap-3 shrink-0">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-red-100 text-base select-none">
            👎
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-semibold text-slate-900">Signaler un faux positif</h3>
            <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
              {meta && (
                <span className={`text-[10px] font-medium rounded-full px-1.5 py-0.5 ${meta.bg} ${meta.color} border ${meta.border}`}>
                  {correction.category} — {meta.label}
                </span>
              )}
              <span className="text-xs text-slate-400">p.{correction.page}</span>
              <span className="text-xs text-slate-500 italic truncate max-w-[200px]">
                «\u202f{correction.original_text.slice(0, 50)}{correction.original_text.length > 50 ? '…' : ''}\u202f»
              </span>
            </div>
          </div>
          <button
            onClick={onCancel}
            className="shrink-0 text-slate-400 hover:text-slate-600 transition-colors"
            aria-label="Fermer"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body — scrollable */}
        <div className="px-5 py-4 space-y-3 overflow-y-auto">
          <p className="text-xs text-slate-500">
            Pourquoi cette correction est-elle incorrecte ?{' '}
            <span className="text-slate-400">(plusieurs raisons possibles — optionnel)</span>
          </p>

          {/* Grille de raisons — cases à cocher */}
          <div className="grid grid-cols-2 gap-2">
            {FEEDBACK_REASONS.map((reason) => {
              const isSelected = selectedReasons.has(reason.code)
              return (
                <button
                  key={reason.code}
                  onClick={() => toggleReason(reason.code)}
                  className={[
                    'text-left rounded-xl border px-3 py-2.5 text-xs transition-all shadow-sm flex items-start gap-2',
                    isSelected
                      ? reason.selectedClass
                      : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50',
                  ].join(' ')}
                >
                  {/* Checkbox visuel */}
                  <div className={[
                    'mt-0.5 shrink-0 h-3.5 w-3.5 rounded border-2 flex items-center justify-center transition-colors',
                    isSelected ? 'bg-red-500 border-red-500' : 'border-slate-300 bg-white',
                  ].join(' ')}>
                    {isSelected && (
                      <svg className="h-2 w-2 text-white" viewBox="0 0 8 8" fill="none">
                        <path d="M1.5 4L3 5.5L6.5 2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                      </svg>
                    )}
                  </div>
                  <div>
                    <div className="font-semibold leading-tight">{reason.label}</div>
                    <div className="text-[10px] text-slate-400 mt-0.5 leading-tight">{reason.desc}</div>
                  </div>
                </button>
              )
            })}
          </div>

          {/* Autre — champ texte libre */}
          <div>
            <button
              onClick={() => toggleReason('other')}
              className={[
                'w-full text-left rounded-xl border px-3 py-2.5 text-xs transition-all flex items-start gap-2',
                selectedReasons.has('other')
                  ? 'border-red-300 bg-red-50 text-red-700 shadow-sm ring-1 ring-red-200'
                  : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50',
              ].join(' ')}
            >
              <div className={[
                'mt-0.5 shrink-0 h-3.5 w-3.5 rounded border-2 flex items-center justify-center transition-colors',
                selectedReasons.has('other') ? 'bg-red-500 border-red-500' : 'border-slate-300 bg-white',
              ].join(' ')}>
                {selectedReasons.has('other') && (
                  <svg className="h-2 w-2 text-white" viewBox="0 0 8 8" fill="none">
                    <path d="M1.5 4L3 5.5L6.5 2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                )}
              </div>
              <div>
                <span className="font-semibold">Autre raison…</span>
                <span className="text-[10px] text-slate-400 ml-1">décrire en quelques mots</span>
              </div>
            </button>
            {selectedReasons.has('other') && (
              <textarea
                ref={textareaRef}
                value={otherText}
                onChange={(e) => setOtherText(e.target.value)}
                placeholder="Ex : l'IA a confondu le nom du personnage avec un homophone…"
                className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-xs text-slate-700 placeholder-slate-300 focus:outline-none focus:ring-1 focus:ring-red-300 focus:border-red-300 resize-none transition-colors"
                rows={2}
              />
            )}
          </div>

          {/* Indicateur de sélection */}
          {selectedReasons.size > 0 && (
            <p className="text-[11px] text-red-500 font-medium">
              {selectedReasons.size} raison{selectedReasons.size > 1 ? 's' : ''} sélectionnée{selectedReasons.size > 1 ? 's' : ''}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-slate-100 flex gap-2 shrink-0">
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="flex-1 rounded-xl bg-red-500 px-4 py-2.5 text-sm font-semibold text-white hover:bg-red-600 disabled:opacity-60 transition-colors"
          >
            {submitting ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Envoi…
              </span>
            ) : 'Signaler ce faux positif'}
          </button>
          <button
            onClick={onCancel}
            className="rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 transition-colors"
          >
            Annuler
          </button>
        </div>
      </div>
    </div>
  )
}
