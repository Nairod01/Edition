'use client'

import { useState } from 'react'
import { CATEGORY_META } from '@/lib/types'

interface CorrItem {
  id: string
  page: number
  category: string
  original_text: string
  corrected_text?: string | null
  description?: string | null
  confidence: string
}

interface JobInfo {
  id: string
  filename: string
  created_at: string | null
  corrections_count: number
}

export interface CompareResult {
  job_a: JobInfo
  job_b: JobInfo
  common: CorrItem[]
  only_a: CorrItem[]
  only_b: CorrItem[]
  stability_score: number
  total_unique: number
  by_category: Record<string, { common: number; only_a: number; only_b: number }>
  same_file: boolean
}

interface Props {
  result: CompareResult
  onClose: () => void
}

function CorrRow({ c, side }: { c: CorrItem; side: 'common' | 'only_a' | 'only_b' }) {
  const meta = CATEGORY_META[c.category]
  const bg =
    side === 'common' ? 'bg-emerald-50/60' :
    side === 'only_a' ? 'bg-red-50/60' : 'bg-blue-50/60'
  return (
    <div className={`flex items-start gap-3 px-4 py-2.5 border-b border-slate-100 last:border-0 ${bg}`}>
      {meta ? (
        <span className={`mt-0.5 shrink-0 inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${meta.bg} ${meta.color}`}>
          {c.category}
        </span>
      ) : (
        <span className="shrink-0 text-xs font-semibold text-stone-500 mt-0.5">{c.category}</span>
      )}
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium text-stone-800">
          <span className="text-stone-400 font-normal">p.{c.page} — </span>
          <span className="font-mono">{c.original_text}</span>
          {c.corrected_text && (
            <span className="text-stone-400"> → <span className="text-stone-600">{c.corrected_text}</span></span>
          )}
        </p>
        {c.description && (
          <p className="text-[11px] text-stone-400 mt-0.5 truncate">{c.description}</p>
        )}
      </div>
    </div>
  )
}

export function CompareModal({ result, onClose }: Props) {
  const [tab, setTab] = useState<'common' | 'only_a' | 'only_b'>('only_a')

  const pct = Math.round(result.stability_score * 100)
  const scoreColor = pct >= 80 ? 'text-emerald-600' : pct >= 60 ? 'text-amber-500' : 'text-red-500'
  const barColor = pct >= 80 ? 'bg-emerald-400' : pct >= 60 ? 'bg-amber-400' : 'bg-red-400'

  const moreCorrections =
    result.job_a.corrections_count >= result.job_b.corrections_count
      ? `A (${result.job_a.corrections_count})`
      : `B (${result.job_b.corrections_count})`

  const conclusion =
    pct >= 85
      ? `Très bonne stabilité (${pct}%). Les ${result.only_a.length + result.only_b.length} corrections différentes sont probablement dues à de légères variations de contexte entre les deux passes.`
      : pct >= 65
      ? `Stabilité modérée (${pct}%). Les ${result.only_a.length + result.only_b.length} corrections non communes méritent une vérification manuelle — certaines peuvent être des oublis réels de l'une des analyses.`
      : `Instabilité détectée (${pct}%). ${result.only_a.length + result.only_b.length} corrections diffèrent entre les deux analyses. Référez-vous à l'analyse ${moreCorrections} comme base, et complétez avec les corrections uniques de l'autre.`

  const tabs: { id: 'common' | 'only_a' | 'only_b'; label: string; count: number }[] = [
    { id: 'only_a', label: 'Uniquement A', count: result.only_a.length },
    { id: 'only_b', label: 'Uniquement B', count: result.only_b.length },
    { id: 'common', label: 'Communes', count: result.common.length },
  ]

  const items =
    tab === 'common' ? result.common :
    tab === 'only_a' ? result.only_a : result.only_b

  const tabColor = (id: typeof tab) =>
    id === 'only_a' ? 'bg-red-100 text-red-600' :
    id === 'only_b' ? 'bg-blue-100 text-blue-600' : 'bg-emerald-100 text-emerald-700'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[88vh] flex flex-col overflow-hidden">

        {/* Header */}
        <div className="px-5 py-4 border-b border-slate-100 flex items-start justify-between">
          <div>
            <p className="text-sm font-semibold text-stone-800">Comparaison d'analyses</p>
            {!result.same_file && (
              <p className="text-[11px] text-amber-500 mt-0.5">⚠ Fichiers différents — la comparaison reste indicative</p>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded-full p-1.5 text-stone-400 hover:text-stone-600 hover:bg-slate-100 transition-colors"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <div className="overflow-y-auto flex-1">

          {/* A vs B */}
          <div className="grid grid-cols-2 gap-3 px-5 py-4 bg-slate-50 border-b border-slate-100">
            {([
              { label: 'Analyse A', job: result.job_a, letter: 'A', chip: 'bg-red-100 text-red-700' },
              { label: 'Analyse B', job: result.job_b, letter: 'B', chip: 'bg-blue-100 text-blue-700' },
            ] as const).map(({ label, job, letter, chip }) => (
              <div key={letter} className="bg-white rounded-xl border border-slate-200 p-3">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${chip}`}>{letter}</span>
                  <span className="text-[11px] font-semibold text-stone-500">{label}</span>
                </div>
                <p className="text-xs font-medium text-stone-800 truncate">{job.filename}</p>
                <p className="text-[11px] text-stone-400 mt-0.5">
                  {job.created_at
                    ? new Date(job.created_at).toLocaleString('fr-FR', {
                        day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
                      })
                    : '—'}
                  {' · '}<span className="font-semibold text-stone-600">{job.corrections_count}</span> corrections
                </p>
              </div>
            ))}
          </div>

          {/* Score de stabilité */}
          <div className="px-5 py-4 border-b border-slate-100">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-stone-600">Score de stabilité</span>
              <span className={`text-xl font-bold tabular-nums ${scoreColor}`}>{pct}%</span>
            </div>
            <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
              <div className={`h-full rounded-full ${barColor} transition-all duration-700`} style={{ width: `${pct}%` }} />
            </div>
            <p className="text-[11px] text-stone-500 mt-2.5 leading-relaxed">{conclusion}</p>
          </div>

          {/* Par catégorie */}
          {Object.keys(result.by_category).length > 0 && (
            <div className="px-5 py-3.5 border-b border-slate-100">
              <p className="text-[11px] font-semibold text-stone-500 uppercase tracking-wide mb-2.5">Par catégorie</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(result.by_category)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([cat, counts]) => {
                    const meta = CATEGORY_META[cat]
                    const total = counts.common + counts.only_a + counts.only_b
                    const catPct = total > 0 ? Math.round((counts.common / total) * 100) : 100
                    const catColor = catPct >= 80 ? 'text-emerald-600' : catPct >= 60 ? 'text-amber-500' : 'text-red-500'
                    return (
                      <div key={cat} className="flex items-center gap-1.5 rounded-lg bg-white border border-slate-200 px-2.5 py-1.5">
                        {meta && (
                          <span className={`inline-flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold ${meta.bg} ${meta.color}`}>
                            {cat}
                          </span>
                        )}
                        <span className={`text-xs font-bold tabular-nums ${catColor}`}>{catPct}%</span>
                        {counts.only_a > 0 && (
                          <span className="text-[10px] text-red-400 font-medium">−{counts.only_a}A</span>
                        )}
                        {counts.only_b > 0 && (
                          <span className="text-[10px] text-blue-400 font-medium">−{counts.only_b}B</span>
                        )}
                      </div>
                    )
                  })}
              </div>
            </div>
          )}

          {/* Onglets */}
          <div className="border-b border-slate-100">
            <div className="flex px-5">
              {tabs.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={[
                    'flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium border-b-2 transition-colors',
                    tab === t.id
                      ? 'border-indigo-400 text-indigo-600'
                      : 'border-transparent text-stone-400 hover:text-stone-600',
                  ].join(' ')}
                >
                  {t.label}
                  <span className={`inline-flex items-center justify-center rounded-full px-1.5 py-px text-[10px] font-bold ${tabColor(t.id)}`}>
                    {t.count}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {/* Liste des corrections */}
          <div>
            {items.length === 0 ? (
              <p className="text-center text-sm text-stone-400 py-10">
                {tab === 'common' ? 'Aucune correction commune.' :
                 tab === 'only_a' ? "Aucune correction exclusive à l'analyse A." :
                 "Aucune correction exclusive à l'analyse B."}
              </p>
            ) : (
              items.map((c) => <CorrRow key={c.id} c={c} side={tab} />)
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
