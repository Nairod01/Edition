'use client'

import { useState, useEffect } from 'react'
import type { UploadResponse, Preset, DocumentMetadata, CommentMode } from '@/lib/types'
import { PRESET_META } from '@/lib/types'

const COMMENT_MODE_KEY = 'editoria_comment_mode'

interface Props {
  estimate: UploadResponse
  onConfirm: (preset: Preset, metadata: DocumentMetadata, commentMode: CommentMode, generatePdf: boolean) => void
  onCancel: () => void
  confirming: boolean
}

export function CostEstimateModal({ estimate, onConfirm, onCancel, confirming }: Props) {
  const [preset, setPreset] = useState<Preset>('complete')
  const [metadata, setMetadata] = useState<DocumentMetadata>({
    author: estimate.pdf_metadata?.author || '',
    title: estimate.pdf_metadata?.title || '',
    characters: '',
    citation_lang: '',
    house_rules: '',
  })

  const [generatePdf, setGeneratePdf] = useState(true)

  const [commentMode, setCommentMode] = useState<CommentMode>(() => {
    if (typeof window !== 'undefined') {
      return (localStorage.getItem(COMMENT_MODE_KEY) as CommentMode) || 'detailed'
    }
    return 'detailed'
  })

  // Persist comment mode to localStorage
  useEffect(() => {
    localStorage.setItem(COMMENT_MODE_KEY, commentMode)
  }, [commentMode])

  // Track which fields were pre-filled from PDF metadata
  const prefilled = {
    author: !!(estimate.pdf_metadata?.author),
    title:  !!(estimate.pdf_metadata?.title),
  }

  // Multiplicateurs de coût par preset — calibrés pour que quick+coherence+facts ≈ complete
  // Pass 1a+1b (ABCD) domine le coût car ~17 appels API parallèles
  // Pass 2 (EFG) = 1-2 grands appels de contexte
  // H = recherches web, coût marginal
  const COST_MULTIPLIERS: Record<Preset, number> = {
    complete:  1.00,
    quick:     0.65,  // Pass 1a + 1b (dominant : nombreux lots)
    coherence: 0.30,  // Pass 2 seulement (1-2 grands appels)
    facts:     0.05,  // Web search (coût minimal)
  }

  const rawCost = estimate.estimated_cost_usd
  const cost = rawCost * COST_MULTIPLIERS[preset]
  const costLabel =
    cost < 0.01
      ? '< 0,01 $'
      : cost.toLocaleString('fr-FR', { style: 'currency', currency: 'USD', maximumFractionDigits: 3 })

  const warningLevel = cost < 0.5 ? 'low' : cost < 2 ? 'medium' : 'high'

  const colorMap: Record<string, { border: string; bg: string; text: string }> = {
    indigo: { border: 'border-sage-500',   bg: 'bg-sage-50',   text: 'text-sage-700'   },
    blue:   { border: 'border-blue-500',   bg: 'bg-blue-50',   text: 'text-blue-700'   },
    green:  { border: 'border-green-500',  bg: 'bg-green-50',  text: 'text-green-700'  },
    amber:  { border: 'border-amber-500',  bg: 'bg-amber-50',  text: 'text-amber-700'  },
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg rounded-3xl bg-white shadow-warm-lg overflow-hidden max-h-[90vh] overflow-y-auto">

        {/* Header */}
        <div className="bg-gradient-to-br from-sage-700 to-sage-500 px-6 py-5">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-white/20">
              <svg className="h-5 w-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 11h.01M12 11h.01M15 11h.01M4.5 19.5h15a2.25 2.25 0 002.25-2.25V6.75A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25v10.5A2.25 2.25 0 004.5 19.5z" />
              </svg>
            </div>
            <div>
              <h2 className="text-lg font-semibold text-white">Paramétrer la correction</h2>
              <p className="text-sm text-sage-200 truncate max-w-xs">{estimate.filename}</p>
            </div>
          </div>
        </div>

        {/* ── 1. Informations sur l'ouvrage (visibles par défaut, en haut) ── */}
        <div className="px-6 pt-5 pb-4 border-b border-stone-100">
          <p className="text-xs font-semibold uppercase tracking-wide text-stone-400 mb-3">
            Informations sur l'ouvrage <span className="normal-case font-normal text-sage-600">(améliore la précision)</span>
          </p>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">
                  Auteur(s){prefilled.author && (
                    <span className="ml-1.5 rounded-full bg-sage-100 text-sage-700 text-[10px] font-semibold px-1.5 py-0.5">PDF</span>
                  )}
                </label>
                <input
                  type="text"
                  value={metadata.author}
                  onChange={(e) => setMetadata({ ...metadata, author: e.target.value })}
                  className={`w-full rounded-lg border px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-sage-300 focus:border-sage-400 ${prefilled.author ? 'border-sage-300 bg-sage-50/40' : 'border-slate-200'}`}
                  placeholder="ex : Stendhal"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">
                  Titre de l'œuvre{prefilled.title && (
                    <span className="ml-1.5 rounded-full bg-sage-100 text-sage-700 text-[10px] font-semibold px-1.5 py-0.5">PDF</span>
                  )}
                </label>
                <input
                  type="text"
                  value={metadata.title}
                  onChange={(e) => setMetadata({ ...metadata, title: e.target.value })}
                  className={`w-full rounded-lg border px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-sage-300 focus:border-sage-400 ${prefilled.title ? 'border-sage-300 bg-sage-50/40' : 'border-slate-200'}`}
                  placeholder="ex : Le Rouge et le Noir"
                />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Personnages / noms propres à protéger</label>
              <input
                type="text"
                value={metadata.characters}
                onChange={(e) => setMetadata({ ...metadata, characters: e.target.value })}
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-sage-300 focus:border-sage-400"
                placeholder="ex : Julien Sorel, Mme de Rênal, Verrières"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Langue des citations</label>
                <input
                  type="text"
                  value={metadata.citation_lang}
                  onChange={(e) => setMetadata({ ...metadata, citation_lang: e.target.value })}
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-sage-300 focus:border-sage-400"
                  placeholder="ex : latin, anglais"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Règles typo maison</label>
                <input
                  type="text"
                  value={metadata.house_rules}
                  onChange={(e) => setMetadata({ ...metadata, house_rules: e.target.value })}
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-sage-300 focus:border-sage-400"
                  placeholder="ex : trait d'union pour État, majuscule Roi"
                />
              </div>
            </div>
          </div>
        </div>

        {/* ── 2. Mode de correction ── */}
        <div className="px-6 pt-4 pb-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-stone-400 mb-3">Mode de correction</p>
          <div className="grid grid-cols-2 gap-2">
            {(Object.entries(PRESET_META) as [Preset, typeof PRESET_META[Preset]][]).map(([key, meta]) => {
              const selected = preset === key
              const colors = colorMap[meta.color]
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setPreset(key)}
                  className={[
                    'flex flex-col items-start gap-1 rounded-xl border-2 p-3 text-left transition-all duration-150',
                    selected
                      ? `${colors.border} ${colors.bg} shadow-warm-sm -translate-y-px`
                      : 'border-stone-200 bg-stone-50 hover:border-sage-300 hover:bg-sage-50/40 hover:-translate-y-px hover:shadow-warm-sm',
                  ].join(' ')}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-base">{meta.icon}</span>
                    <span className={`text-sm font-semibold ${selected ? colors.text : 'text-slate-700'}`}>
                      {meta.label}
                    </span>
                  </div>
                  <p className="text-xs text-slate-500 leading-snug">{meta.desc}</p>
                  <div className="flex flex-wrap gap-1 mt-1">
                    {meta.categories.map((cat) => (
                      <span
                        key={cat}
                        className={`text-[10px] font-bold rounded px-1 py-0.5 ${selected ? `${colors.bg} ${colors.text}` : 'bg-slate-100 text-slate-500'}`}
                      >
                        {cat}
                      </span>
                    ))}
                  </div>
                </button>
              )
            })}
          </div>
        </div>

        {/* ── 3. Stats ── */}
        <div className="grid grid-cols-3 divide-x divide-slate-100 border-y border-slate-100">
          <Stat label="Pages" value={estimate.pages.toLocaleString('fr-FR')} />
          <Stat label="Mots" value={estimate.words.toLocaleString('fr-FR')} />
          <Stat label="Corrections estimées" value={`~${Math.round(estimate.estimated_corrections * COST_MULTIPLIERS[preset])}`} />
        </div>

        {/* ── 4. Coût ── */}
        <div className="px-6 py-4">
          <div className={[
            'flex items-start gap-3 rounded-xl p-4',
            warningLevel === 'low' ? 'bg-green-50 border border-green-200' :
            warningLevel === 'medium' ? 'bg-amber-50 border border-amber-200' :
            'bg-red-50 border border-red-200',
          ].join(' ')}>
            <span className="text-2xl mt-0.5">
              {warningLevel === 'low' ? '✅' : warningLevel === 'medium' ? '⚠️' : '🔴'}
            </span>
            <div>
              <p className={[
                'font-semibold text-base',
                warningLevel === 'low' ? 'text-green-800' :
                warningLevel === 'medium' ? 'text-amber-800' : 'text-red-800',
              ].join(' ')}>
                Coût estimé Claude API : <span className="font-bold">{costLabel}</span>
              </p>
              <p className={[
                'text-sm mt-1',
                warningLevel === 'low' ? 'text-green-700' :
                warningLevel === 'medium' ? 'text-amber-700' : 'text-red-700',
              ].join(' ')}>
                Environ {estimate.estimated_tokens.toLocaleString('fr-FR')} tokens (entrée + sortie estimée).
                {warningLevel === 'high' && ' Document volumineux — le coût réel peut varier.'}
              </p>
            </div>
          </div>
        </div>

        {/* ── 5. Mode commentaires ── */}
        <div className="px-6 pb-4">
          <p className="text-xs font-medium text-stone-600 mb-2">Format des commentaires dans le PDF</p>
          <div className="flex rounded-xl overflow-hidden border border-stone-200">
            <button
              onClick={() => setCommentMode('simple')}
              className={`flex-1 px-4 py-2.5 text-xs font-medium transition-all duration-150 ${
                commentMode === 'simple'
                  ? 'bg-sage-600 text-white'
                  : 'bg-stone-50 text-stone-600 hover:bg-stone-100'
              }`}
            >
              <span className="block font-semibold">Simple</span>
              <span className="block text-[10px] opacity-75 mt-0.5">Type d'erreur + correction directe</span>
            </button>
            <button
              onClick={() => setCommentMode('detailed')}
              className={`flex-1 px-4 py-2.5 text-xs font-medium transition-all duration-150 border-l border-stone-200 ${
                commentMode === 'detailed'
                  ? 'bg-sage-600 text-white'
                  : 'bg-stone-50 text-stone-600 hover:bg-stone-100'
              }`}
            >
              <span className="block font-semibold">Détaillé</span>
              <span className="block text-[10px] opacity-75 mt-0.5">Règle grammaticale + explication</span>
            </button>
          </div>
        </div>

        {/* ── 6. Option PDF ── */}
        <div className="px-6 pb-4">
          <label className="flex items-start gap-3 cursor-pointer group">
            <input
              type="checkbox"
              checked={generatePdf}
              onChange={e => setGeneratePdf(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-stone-300 text-sage-600 focus:ring-sage-400 cursor-pointer"
            />
            <div>
              <p className="text-sm font-medium text-stone-700 group-hover:text-stone-900 transition-colors">
                Générer le PDF annoté
              </p>
              <p className="text-xs text-stone-400 mt-0.5">
                Ajoute les annotations dans le PDF. Décochez pour obtenir uniquement le rapport Word (.docx).
              </p>
            </div>
          </label>
        </div>

        {/* ── 7. Actions ── */}
        <div className="flex gap-3 px-6 pb-6">
          <button
            onClick={onCancel}
            disabled={confirming}
            className="btn-secondary flex-1 py-3"
          >
            Annuler
          </button>
          <button
            onClick={() => onConfirm(preset, metadata, commentMode, generatePdf)}
            disabled={confirming}
            className="btn-primary flex-[2] py-3"
          >
            {confirming ? (
              <>
                <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Démarrage…
              </>
            ) : (
              <>
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
                Lancer la correction
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col items-center py-4 px-2">
      <span className="text-xl font-bold text-stone-800">{value}</span>
      <span className="text-xs text-stone-500 mt-0.5 text-center">{label}</span>
    </div>
  )
}
