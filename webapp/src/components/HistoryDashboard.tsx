'use client'

import { useCallback, useEffect, useState } from 'react'
import type { DashboardData } from '@/lib/types'
import { CATEGORY_META, FEEDBACK_REASONS } from '@/lib/types'
import { CompareModal, type CompareResult } from '@/components/CompareModal'
import { apiFetch } from '@/lib/auth'

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'

const REASON_LABELS: Record<string, string> = {
  ...Object.fromEntries(FEEDBACK_REASONS.map((r) => [r.code, r.label])),
  unspecified: 'Non précisé',
}

interface Props {
  onOpenJob: (jobId: string) => void
}

export function HistoryDashboard({ onOpenJob }: Props) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState<'json' | 'csv' | null>(null)
  const [compareMode, setCompareMode] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [comparing, setComparing] = useState(false)
  const [compareResult, setCompareResult] = useState<CompareResult | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/dashboard`)
      if (!res.ok) throw new Error()
      setData(await res.json())
    } catch {
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  function toggleSelect(id: string) {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : prev.length < 2 ? [...prev, id] : [prev[1], id]
    )
  }

  function exitCompareMode() {
    setCompareMode(false)
    setSelected([])
    setCompareResult(null)
  }

  async function runCompare() {
    if (selected.length !== 2) return
    setComparing(true)
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/compare?job_a=${selected[0]}&job_b=${selected[1]}`)
      if (!res.ok) throw new Error()
      setCompareResult(await res.json())
    } catch {
      alert('Erreur lors de la comparaison.')
    } finally {
      setComparing(false)
    }
  }

  async function handleExport(format: 'json' | 'csv') {
    setExporting(format)
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/dashboard/export?format=${format}`)
      if (!res.ok) throw new Error()
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `faux_positifs_export.${format}`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      alert("Erreur lors de l'export.")
    } finally {
      setExporting(null)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <svg className="h-6 w-6 animate-spin text-indigo-300" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="text-center py-24 text-sm text-stone-400">
        Impossible de charger les statistiques.{' '}
        <button onClick={load} className="text-sage-600 hover:underline">
          Réessayer
        </button>
      </div>
    )
  }

  const fpRate =
    data.total_corrections > 0
      ? ((data.total_false_positives / data.total_corrections) * 100).toFixed(1)
      : '0'

  const unlocatedRate =
    data.total_corrections > 0
      ? (((data.total_unlocated ?? 0) / data.total_corrections) * 100).toFixed(1)
      : '0'

  const hasFpData = Object.keys(data.by_reason).length > 0 || Object.keys(data.by_category).length > 0

  return (
    <>
    <div className="space-y-6">

      {/* Résumé chiffré */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { value: data.total_jobs, label: 'Analyses réalisées', color: 'text-stone-900' },
          { value: data.total_corrections.toLocaleString('fr-FR'), label: 'Corrections générées', color: 'text-stone-900' },
          {
            value: `${data.total_false_positives} (${fpRate}\u202f%)`,
            label: 'Faux positifs déclarés',
            color: data.total_false_positives > 0 ? 'text-red-500' : 'text-stone-900',
          },
          {
            value: `${(data.total_unlocated ?? 0).toLocaleString('fr-FR')} (${unlocatedRate}\u202f%)`,
            label: 'Non localisées PDF',
            color: (data.total_unlocated ?? 0) > 0 ? 'text-orange-500' : 'text-stone-900',
          },
        ].map((stat) => (
          <div key={stat.label} className="rounded-2xl bg-white border border-stone-200 p-4 text-center shadow-warm-sm">
            <p className={`text-xl font-bold ${stat.color}`}>{stat.value}</p>
            <p className="text-[11px] text-stone-500 mt-0.5 leading-tight">{stat.label}</p>
          </div>
        ))}
      </div>

      {/* Raisons des faux positifs */}
      {hasFpData && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">

          {/* Par raison */}
          {Object.keys(data.by_reason).length > 0 && (
            <div className="rounded-2xl bg-white border border-stone-200 shadow-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-slate-100 flex items-center justify-between">
                <p className="text-sm font-semibold text-stone-700">Raisons des faux positifs</p>
                <div className="flex gap-1.5">
                  <button
                    onClick={() => handleExport('csv')}
                    disabled={exporting !== null}
                    className="text-[11px] rounded-lg border border-stone-200 px-2.5 py-1 text-stone-500 hover:bg-slate-50 disabled:opacity-50 transition-colors"
                  >
                    {exporting === 'csv' ? '…' : '↓ CSV'}
                  </button>
                  <button
                    onClick={() => handleExport('json')}
                    disabled={exporting !== null}
                    className="text-[11px] rounded-lg border border-stone-200 px-2.5 py-1 text-stone-500 hover:bg-slate-50 disabled:opacity-50 transition-colors"
                  >
                    {exporting === 'json' ? '…' : '↓ JSON'}
                  </button>
                </div>
              </div>
              <div className="divide-y divide-slate-100">
                {Object.entries(data.by_reason)
                  .sort(([, a], [, b]) => b - a)
                  .map(([code, count]) => {
                    const label = REASON_LABELS[code] || code
                    const pct =
                      data.total_false_positives > 0
                        ? (count / data.total_false_positives) * 100
                        : 0
                    return (
                      <div key={code} className="flex items-center gap-3 px-4 py-2.5">
                        <span className="flex-1 text-xs text-stone-700 truncate">{label}</span>
                        <div className="hidden sm:block h-1.5 w-20 rounded-full bg-slate-100">
                          <div
                            className="h-full rounded-full bg-red-300"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <span className="text-sm font-semibold text-red-500 w-5 text-right shrink-0">
                          {count}
                        </span>
                      </div>
                    )
                  })}
              </div>
            </div>
          )}

          {/* Par catégorie */}
          {Object.keys(data.by_category).length > 0 && (
            <div className="rounded-2xl bg-white border border-stone-200 shadow-sm overflow-hidden">
              <div className="px-5 py-3.5 border-b border-slate-100">
                <p className="text-sm font-semibold text-stone-700">Par catégorie</p>
              </div>
              <div className="divide-y divide-slate-100">
                {Object.entries(data.by_category)
                  .sort(([a], [b]) => a.localeCompare(b))
                  .map(([cat, count]) => {
                    const meta = CATEGORY_META[cat]
                    const total = Object.values(data.by_category).reduce((s, v) => s + v, 0)
                    const pct = total > 0 ? (count / total) * 100 : 0
                    return (
                      <div key={cat} className="flex items-center gap-3 px-4 py-2.5">
                        {meta ? (
                          <span
                            className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${meta.bg} ${meta.color}`}
                          >
                            {cat}
                          </span>
                        ) : (
                          <span className="text-xs font-semibold text-stone-500">{cat}</span>
                        )}
                        <span className="flex-1 text-xs text-stone-700 truncate">
                          {meta?.label || cat}
                        </span>
                        <div className="hidden sm:block h-1.5 w-20 rounded-full bg-slate-100">
                          <div
                            className={`h-full rounded-full ${meta?.dot || 'bg-slate-300'}`}
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <span className={`text-sm font-semibold w-5 text-right shrink-0 ${meta?.color || 'text-slate-600'}`}>
                          {count}
                        </span>
                      </div>
                    )
                  })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Historique des analyses */}
      <div className="rounded-2xl bg-white border border-stone-200 shadow-sm overflow-hidden">
        <div className="px-5 py-3.5 border-b border-slate-100 flex items-center justify-between gap-2">
          <p className="text-sm font-semibold text-stone-700">Historique des analyses</p>
          <div className="flex items-center gap-2">
            {compareMode ? (
              <>
                {selected.length === 2 && (
                  <button
                    onClick={runCompare}
                    disabled={comparing}
                    className="text-[11px] rounded-lg border border-indigo-300 bg-indigo-50 px-2.5 py-1 text-indigo-700 font-medium hover:bg-indigo-100 disabled:opacity-50 transition-colors flex items-center gap-1"
                  >
                    {comparing ? (
                      <svg className="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                    ) : '⚖'}
                    {comparing ? 'Analyse…' : 'Analyser les différences'}
                  </button>
                )}
                {!comparing && selected.length < 2 && (
                  <span className="text-[11px] text-stone-400">
                    {selected.length === 0 ? 'Sélectionne 2 analyses' : 'Sélectionne une 2ème analyse'}
                  </span>
                )}
                <button
                  onClick={exitCompareMode}
                  className="text-[11px] text-stone-400 hover:text-red-500 transition-colors"
                >
                  Annuler
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => setCompareMode(true)}
                  className="text-[11px] rounded-lg border border-stone-200 px-2.5 py-1 text-stone-500 hover:bg-slate-50 transition-colors flex items-center gap-1"
                  title="Comparer deux analyses"
                >
                  ⚖ Comparer
                </button>
                <button
                  onClick={load}
                  className="text-[11px] text-stone-400 hover:text-indigo-500 transition-colors flex items-center gap-1"
                  title="Actualiser"
                >
                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  Actualiser
                </button>
              </>
            )}
          </div>
        </div>

        {data.jobs.length === 0 ? (
          <p className="px-5 py-10 text-center text-sm text-stone-400">
            Aucune analyse pour le moment.
          </p>
        ) : (
          <div className="divide-y divide-slate-100">
            {data.jobs.map((job) => {
              const isSelected = selected.includes(job.id)
              const selIdx = selected.indexOf(job.id)
              const selLabel = selIdx === 0 ? 'A' : selIdx === 1 ? 'B' : null
              return (
                <div
                  key={job.id}
                  className={[
                    'flex items-center gap-3 px-5 py-3 transition-colors',
                    compareMode && isSelected ? 'bg-indigo-50/50' : '',
                    compareMode && job.status !== 'done' ? 'opacity-40 pointer-events-none' : '',
                  ].join(' ')}
                >
                  {compareMode && job.status === 'done' && (
                    <button
                      onClick={() => toggleSelect(job.id)}
                      className={[
                        'shrink-0 h-6 w-6 rounded-full border-2 flex items-center justify-center text-[10px] font-bold transition-all',
                        isSelected
                          ? 'border-indigo-500 bg-indigo-500 text-white'
                          : 'border-stone-300 text-transparent hover:border-indigo-300',
                      ].join(' ')}
                      title={isSelected ? 'Désélectionner' : 'Sélectionner pour comparer'}
                    >
                      {selLabel ?? ''}
                    </button>
                  )}

                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-stone-800 truncate">{job.filename}</p>
                    <p className="text-xs text-stone-400 mt-0.5 flex items-center gap-1.5 flex-wrap">
                      {job.created_at
                        ? new Date(job.created_at).toLocaleString('fr-FR', {
                            day: '2-digit', month: 'short', year: 'numeric',
                            hour: '2-digit', minute: '2-digit',
                          })
                        : '—'}
                      {job.corrections_count > 0 && (
                        <span>· {job.corrections_count} correction{job.corrections_count > 1 ? 's' : ''}</span>
                      )}
                      {job.false_positives_count > 0 && (
                        <>
                          <span className="text-red-400">· {job.false_positives_count} FP</span>
                          {job.corrections_count > 0 && (
                            <span className="inline-flex items-center rounded-full bg-red-50 border border-red-200 px-1.5 py-px text-[10px] font-semibold text-red-500">
                              {((job.false_positives_count / job.corrections_count) * 100).toFixed(1)}%
                            </span>
                          )}
                        </>
                      )}
                      {(job.unlocated_count ?? 0) > 0 && (
                        <>
                          <span className="text-orange-400">· {job.unlocated_count} non localisée{job.unlocated_count > 1 ? 's' : ''}</span>
                          {job.corrections_count > 0 && (
                            <span className="inline-flex items-center rounded-full bg-orange-50 border border-orange-200 px-1.5 py-px text-[10px] font-semibold text-orange-500">
                              {((job.unlocated_count / job.corrections_count) * 100).toFixed(1)}%
                            </span>
                          )}
                        </>
                      )}
                      {job.actual_cost_usd != null && (
                        <span className="font-mono text-stone-500">· ${job.actual_cost_usd.toFixed(3)}</span>
                      )}
                    </p>
                  </div>

                  <span className={[
                    'shrink-0 text-[11px] font-medium rounded-full px-2 py-0.5',
                    job.status === 'done' ? 'bg-green-100 text-green-700' :
                    job.status === 'error' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700',
                  ].join(' ')}>
                    {job.status === 'done' ? 'Terminé' : job.status === 'error' ? 'Erreur' : job.status}
                  </span>

                  {!compareMode && job.status === 'done' && (
                    <button
                      onClick={() => onOpenJob(job.id)}
                      className="shrink-0 text-[11px] rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-sage-700 hover:bg-sage-100 transition-all duration-150"
                    >
                      Voir
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>

    {compareResult && (
      <CompareModal result={compareResult} onClose={() => setCompareResult(null)} />
    )}
    </>
  )
}
