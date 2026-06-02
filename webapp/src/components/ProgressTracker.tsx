'use client'

import { useEffect, useRef, useState } from 'react'
import type { JobState } from '@/lib/types'
import { CATEGORY_META } from '@/lib/types'

interface Props {
  job: JobState
  startedAt: number
}

const STEPS = [
  { key: 'extract',  label: 'Extraction du texte',               minPct: 5,  maxPct: 15  },
  { key: 'claude',   label: 'Analyse éditoriale (Claude)',        minPct: 15, maxPct: 75  },
  { key: 'tavily',   label: 'Vérification des faits (Web Search)', minPct: 75, maxPct: 88  },
  { key: 'annotate', label: 'Génération du PDF annoté',           minPct: 88, maxPct: 100 },
]

const TIPS = [
  "La relecture à voix haute est l'une des techniques les plus efficaces pour détecter les fautes que l'œil ne voit plus.",
  "Un bon correcteur lit le texte au moins trois fois : une pour le sens, une pour la langue, une pour la mise en page.",
  "L'apostrophe typographique (') est différente de l'apostrophe droite ('). Seule la première est correcte en français.",
  "En français, les guillemets « » doivent être accompagnés d'espaces insécables à l'intérieur.",
  "Une virgule entre le sujet et le verbe est toujours une faute en français, sauf dans les incises.",
  "ÉditorIA analyse votre document en trois passes successives pour ne manquer aucune erreur.",
  "La catégorie F (Uniformisation) compare chaque occurrence d'un terme à travers tout le document.",
  "Un placeholder oublié (XX, TBD, À compléter) peut passer à l'impression sans être détecté.",
  "La confusion entre « ou » (disjonction) et « où » (lieu) est l'une des fautes les plus fréquentes en édition.",
  "Le tiret cadratin (—) s'utilise pour les dialogues et les incises. Le demi-cadratin (–) pour les intervalles.",
  "L'ellipse typographique (…) est un caractère unique, différent de trois points successifs (...).",
  "En typographie française, la ligature « œ » est obligatoire dans « cœur », « sœur », « œuvre ».",
  "Un correcteur professionnel consacre en moyenne 1 à 2 heures par tranche de 10 000 mots.",
  "Le renvoi G est souvent la correction la plus négligée : une note absente à la première occurrence peut tromper le lecteur.",
  "Préciser le titre et l'auteur de l'ouvrage dans les paramètres améliore significativement la précision de la correction.",
  "Les règles typographiques maison (majuscule Président, tiret dans État) priment sur les règles générales.",
  "La catégorie E (Sémantique) détecte les mots employés à contre-sens, même si leur orthographe est correcte.",
  "Pour les romans, ÉditorIA respecte automatiquement le style de l'auteur et ne corrige pas les tournures délibérées.",
]

function getActiveStepIdx(progress: number): number {
  for (let i = STEPS.length - 1; i >= 0; i--) {
    if (progress >= STEPS[i].minPct) return i
  }
  return 0
}

function formatRemaining(seconds: number): string {
  if (seconds < 5)  return 'presque terminé…'
  if (seconds < 60) return `~${Math.round(seconds)} s`
  const min = Math.round(seconds / 60)
  return `~${min} min`
}

export function ProgressTracker({ job, startedAt }: Props) {
  const [elapsed, setElapsed] = useState(0)
  const [tipIdx, setTipIdx] = useState(() => Math.floor(Math.random() * TIPS.length))
  const [tipVisible, setTipVisible] = useState(true)
  const rafRef   = useRef<ReturnType<typeof setInterval> | null>(null)
  const tipRef   = useRef<ReturnType<typeof setInterval> | null>(null)

  // Ticker — temps écoulé
  useEffect(() => {
    if (job.status === 'done' || job.status === 'error') {
      if (rafRef.current) clearInterval(rafRef.current)
      return
    }
    rafRef.current = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000))
    }, 1000)
    return () => { if (rafRef.current) clearInterval(rafRef.current) }
  }, [job.status, startedAt])

  // Rotation des tips toutes les 7s avec fondu
  useEffect(() => {
    if (job.status === 'done' || job.status === 'error') {
      if (tipRef.current) clearInterval(tipRef.current)
      return
    }
    tipRef.current = setInterval(() => {
      setTipVisible(false)
      setTimeout(() => {
        setTipIdx((i) => (i + 1) % TIPS.length)
        setTipVisible(true)
      }, 400)
    }, 7000)
    return () => { if (tipRef.current) clearInterval(tipRef.current) }
  }, [job.status])

  const isDone    = job.status === 'done'
  const isError   = job.status === 'error'
  const isRunning = !isDone && !isError
  const activeIdx = getActiveStepIdx(job.progress)

  // Estimation du temps restant
  let remainingLabel = ''
  if (isRunning && job.progress > 5 && elapsed > 5) {
    const speed = job.progress / elapsed
    const remaining = (100 - job.progress) / speed
    remainingLabel = formatRemaining(remaining)
  }

  return (
    <div className="w-full max-w-xl mx-auto space-y-4">

      {/* ── Encart principal d'analyse ── */}
      {isRunning && (
        <div className="rounded-2xl bg-indigo-600 text-white px-6 py-5 shadow-lg">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white/20">
              <svg className="h-5 w-5 animate-spin text-white" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
              </svg>
            </div>
            <div>
              <p className="font-semibold text-lg leading-tight">Ouvrage en cours d'analyse</p>
              {remainingLabel && (
                <p className="text-indigo-200 text-sm">Temps restant estimé : {remainingLabel}</p>
              )}
            </div>
          </div>

          {/* Barre de progression intégrée */}
          <div className="mb-4">
            <div className="flex items-center justify-between text-sm text-indigo-100 mb-1.5">
              <span className="truncate max-w-xs opacity-90">{job.progress_label}</span>
              <span className="font-bold ml-2 shrink-0">{job.progress}%</span>
            </div>
            <div className="h-2 w-full rounded-full bg-white/20 overflow-hidden">
              <div
                className="h-full rounded-full bg-white transition-all duration-700"
                style={{ width: `${job.progress}%` }}
              />
            </div>
          </div>

          {/* Le saviez-vous */}
          <div className="border-t border-white/20 pt-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-indigo-300 mb-1.5">
              Le saviez-vous ?
            </p>
            <p
              className="text-sm text-indigo-100 leading-relaxed transition-opacity duration-400"
              style={{ opacity: tipVisible ? 1 : 0 }}
            >
              {TIPS[tipIdx]}
            </p>
          </div>
        </div>
      )}

      {/* ── Fiche document ── */}
      <div className="flex items-center gap-3 rounded-xl bg-white border border-slate-200 px-4 py-3 shadow-sm">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50">
          <svg className="h-5 w-5 text-indigo-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
          </svg>
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-slate-800 truncate">{job.filename}</p>
          <p className="text-xs text-slate-500">
            {job.pages_count ? `${job.pages_count} pages` : ''}
            {job.word_count  ? ` · ${job.word_count.toLocaleString('fr-FR')} mots` : ''}
          </p>
        </div>
        {isRunning && elapsed > 0 && (
          <span className="text-xs text-slate-400 shrink-0">
            {Math.floor(elapsed / 60) > 0
              ? `${Math.floor(elapsed / 60)} min ${elapsed % 60} s`
              : `${elapsed} s`}
          </span>
        )}
      </div>

      {/* ── Erreur ── */}
      {isError && (
        <div className="rounded-xl bg-red-50 border border-red-200 p-4">
          <p className="font-semibold text-red-800">Erreur lors du traitement</p>
          <p className="text-sm text-red-700 mt-1">
            {job.error_message || 'Une erreur inattendue est survenue.'}
          </p>
        </div>
      )}

      {/* ── Étapes (hors encart principal, visibles en dessous) ── */}
      {!isError && (
        <div className="space-y-1">
          {STEPS.map((step, idx) => {
            const done   = job.progress >= step.maxPct || isDone
            const active = !isDone && idx === activeIdx
            return (
              <div
                key={step.key}
                className={[
                  'flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors',
                  done   ? 'text-slate-700' : active ? 'text-indigo-700 bg-indigo-50' : 'text-slate-400',
                ].join(' ')}
              >
                <div className={[
                  'flex h-5 w-5 shrink-0 items-center justify-center rounded-full',
                  done   ? 'bg-indigo-500' :
                  active ? 'border-2 border-indigo-400' :
                           'border-2 border-slate-300',
                ].join(' ')}>
                  {done ? (
                    <svg className="h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : active ? (
                    <div className="h-1.5 w-1.5 rounded-full bg-indigo-400 animate-pulse" />
                  ) : null}
                </div>
                <span className={active ? 'font-medium' : ''}>{step.label}</span>
                {active && (
                  <svg className="ml-auto h-4 w-4 shrink-0 animate-spin text-indigo-500" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                  </svg>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* ── Compteur live par catégorie ── */}
      {job.corrections_count > 0 && (
        <div className="rounded-xl bg-white border border-slate-200 p-4 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-3">
            {isDone
              ? `${job.corrections_count} correction${job.corrections_count > 1 ? 's' : ''} au total`
              : `${job.corrections_count} correction${job.corrections_count > 1 ? 's' : ''} trouvée${job.corrections_count > 1 ? 's' : ''} jusqu'ici`
            }
          </p>
          <div className="flex flex-wrap gap-2">
            {Object.entries(job.corrections_by_category)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([cat, count]) => {
                const meta = CATEGORY_META[cat]
                if (!meta || count === 0) return null
                return (
                  <span
                    key={cat}
                    className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${meta.bg} ${meta.color} border ${meta.border}`}
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
                    {cat} — {meta.label} · <strong>{count}</strong>
                  </span>
                )
              })}
          </div>
        </div>
      )}
    </div>
  )
}
