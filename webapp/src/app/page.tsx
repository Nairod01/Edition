'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { UploadZone } from '@/components/UploadZone'
import { CostEstimateModal } from '@/components/CostEstimateModal'
import { ProgressTracker } from '@/components/ProgressTracker'
import { ResultCard } from '@/components/ResultCard'
import { HistoryDashboard } from '@/components/HistoryDashboard'
import { LoginPage } from '@/components/LoginPage'
import { AdminPanel } from '@/components/AdminPanel'
import type { DocType, JobState, UploadResponse, Preset, DocumentMetadata, CommentMode } from '@/lib/types'
import { getStoredUser, clearAuth, apiFetch, getToken } from '@/lib/auth'
import type { AuthUser } from '@/lib/auth'

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'
const POLL_INTERVAL_MS = 2000

type Phase = 'upload' | 'estimating' | 'confirming' | 'processing' | 'done' | 'error'
type ActiveTab = 'analyse' | 'historique' | 'admin'

export default function Home() {
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)
  const [authLoading, setAuthLoading] = useState(true)

  const [phase, setPhase] = useState<Phase>('upload')
  const [uploading, setUploading] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [estimate, setEstimate] = useState<UploadResponse | null>(null)
  const [job, setJob] = useState<JobState | null>(null)
  const [startedAt, setStartedAt] = useState<number>(Date.now())
  const [preset, setPreset] = useState<Preset>('complete')
  const [metadata, setMetadata] = useState<DocumentMetadata>({ author: '', title: '', characters: '', citation_lang: '', house_rules: '' })
  const [commentMode, setCommentMode] = useState<CommentMode>('detailed')
  const [activeTab, setActiveTab] = useState<ActiveTab>('analyse')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Auth check on mount ───────────────────────────────────────────────────
  useEffect(() => {
    const stored = getStoredUser()
    if (!stored || !getToken()) {
      setAuthLoading(false)
      return
    }
    // Validate token with backend and refresh user info
    apiFetch(`${BACKEND_URL}/api/auth/me`)
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data) setAuthUser(data)
        else { clearAuth(); setAuthUser(null) }
      })
      .catch(() => { clearAuth(); setAuthUser(null) })
      .finally(() => setAuthLoading(false))
  }, [])

  // ── Polling ───────────────────────────────────────────────────────────────
  const startPolling = useCallback((jobId: string) => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const res = await apiFetch(`${BACKEND_URL}/api/jobs/${jobId}`)
        if (!res.ok) return
        const data: JobState = await res.json()
        setJob(data)
        if (data.status === 'done') {
          setPhase('done')
          clearInterval(pollRef.current!)
          // Refresh user credits after pipeline completes
          apiFetch(`${BACKEND_URL}/api/auth/me`)
            .then(r => r.ok ? r.json() : null)
            .then(d => { if (d) setAuthUser(d) })
            .catch(() => {})
        } else if (data.status === 'error') {
          setPhase('error')
          clearInterval(pollRef.current!)
        }
      } catch {
        // silently retry
      }
    }, POLL_INTERVAL_MS)
  }, [])

  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  // ── Step 1: Upload PDF ────────────────────────────────────────────────────
  const handleUpload = useCallback(async (file: File, docType: DocType) => {
    setUploading(true)
    setPhase('estimating')
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('doc_type', docType)
      const res = await apiFetch(`${BACKEND_URL}/api/upload`, {
        method: 'POST',
        body: formData,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Erreur réseau' }))
        throw new Error(err.detail || "Erreur lors de l'envoi")
      }
      const data: UploadResponse = await res.json()
      setEstimate(data)
      setPhase('confirming')
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Erreur lors de l'envoi du fichier.")
      setPhase('upload')
    } finally {
      setUploading(false)
    }
  }, [])

  // ── Step 2: Confirm and start pipeline ────────────────────────────────────
  const handleConfirm = useCallback(async (selectedPreset: Preset, selectedMetadata: DocumentMetadata, selectedCommentMode: CommentMode = 'detailed', generatePdf: boolean = true) => {
    if (!estimate) return
    setPreset(selectedPreset)
    setMetadata(selectedMetadata)
    setCommentMode(selectedCommentMode)
    setConfirming(true)
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/jobs/${estimate.job_id}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          preset: selectedPreset,
          comment_mode: selectedCommentMode,
          generate_pdf: generatePdf,
          metadata: {
            author: selectedMetadata.author || null,
            title: selectedMetadata.title || null,
            characters: selectedMetadata.characters || null,
            citation_lang: selectedMetadata.citation_lang || null,
            house_rules: selectedMetadata.house_rules || null,
          },
        }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Impossible de démarrer le traitement.')
      }
      setPhase('processing')
      setStartedAt(Date.now())
      setJob({
        id: estimate.job_id,
        filename: estimate.filename,
        status: 'pending',
        progress: 0,
        progress_label: 'Démarrage…',
        pages_count: estimate.pages,
        word_count: estimate.words,
        estimated_cost_usd: estimate.estimated_cost_usd,
        corrections_count: 0,
        corrections_by_category: {},
        error_message: null,
        created_at: null,
        doc_type: estimate.doc_type,
      })
      startPolling(estimate.job_id)
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : 'Erreur.')
    } finally {
      setConfirming(false)
    }
  }, [estimate, startPolling])

  // ── Step 2 alt: Cancel ────────────────────────────────────────────────────
  const handleCancel = useCallback(async () => {
    if (estimate) {
      try {
        await apiFetch(`${BACKEND_URL}/api/jobs/${estimate.job_id}/cancel`, { method: 'DELETE' })
      } catch { /* ignore */ }
    }
    setEstimate(null)
    setPhase('upload')
  }, [estimate])

  // ── Ouvrir un job depuis l'historique ────────────────────────────────────
  const handleOpenJob = useCallback(async (jobId: string) => {
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/jobs/${jobId}`)
      if (!res.ok) throw new Error()
      const data: JobState = await res.json()
      setJob(data)
      setPhase('done')
      setActiveTab('analyse')
    } catch {
      alert('Impossible de charger cette analyse.')
    }
  }, [])

  // ── Reset ─────────────────────────────────────────────────────────────────
  const handleReset = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current)
    setPhase('upload')
    setEstimate(null)
    setJob(null)
    setUploading(false)
    setConfirming(false)
    setActiveTab('analyse')
  }, [])

  // ── Retry (error → confirming modal with same PDF) ─────────────────────
  const handleRetry = useCallback(async () => {
    if (!estimate) { handleReset(); return }
    try {
      await apiFetch(`${BACKEND_URL}/api/jobs/${estimate.job_id}/reset`, { method: 'POST' })
    } catch { /* ignore */ }
    setJob(null)
    setPhase('confirming')
  }, [estimate, handleReset])

  // ── Auth loading ──────────────────────────────────────────────────────────
  if (authLoading) {
    return (
      <div className="min-h-screen bg-stone-50 flex items-center justify-center">
        <svg className="h-8 w-8 animate-spin text-sage-300" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      </div>
    )
  }

  // ── Not authenticated ─────────────────────────────────────────────────────
  if (!authUser) {
    return <LoginPage onLogin={setAuthUser} />
  }

  // ── Credit indicator ──────────────────────────────────────────────────────
  const creditPct = authUser.limit_eur && authUser.limit_eur > 0
    ? Math.min(100, Math.round((authUser.spent_eur / authUser.limit_eur) * 100))
    : null
  const creditColor = creditPct === null ? 'text-stone-400'
    : creditPct >= 90 ? 'text-red-600'
    : creditPct >= 70 ? 'text-amber-600'
    : 'text-emerald-600'

  return (
    <main className="min-h-screen bg-stone-50">
      {/* Header */}
      <header className="border-b border-stone-200 bg-white shadow-warm-sm sticky top-0 z-40">
        <div className="mx-auto flex max-w-4xl items-center gap-3 px-4 py-3">
          <button
            onClick={handleReset}
            className="flex items-center gap-3 group"
            title="Retour à l'accueil"
          >
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-sage-600 group-hover:bg-sage-700 transition-all duration-150 shadow-warm-sm group-hover:shadow-warm-md group-hover:-translate-y-px">
              <svg className="h-5 w-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10" />
              </svg>
            </div>
            <div className="text-left">
              <h1 className="text-lg font-bold text-stone-900 group-hover:text-sage-700 transition-colors">ÉditorIA</h1>
              <p className="text-[11px] text-stone-400 leading-none">Correction éditoriale PDF</p>
            </div>
          </button>

          <div className="ml-auto flex items-center gap-2 text-xs">
            {/* Credit pill */}
            {authUser.limit_eur ? (
              <span className={`hidden sm:inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 border text-[11px] font-medium tabular-nums ${
                creditPct !== null && creditPct >= 90
                  ? 'bg-red-50 border-red-200 text-red-700'
                  : creditPct !== null && creditPct >= 70
                  ? 'bg-amber-50 border-amber-200 text-amber-700'
                  : 'bg-emerald-50 border-emerald-200 text-emerald-700'
              }`} title="Crédits consommés ce mois">
                💳 {authUser.credits_remaining_eur?.toFixed(2)}€ restants
              </span>
            ) : null}

            {/* User pill */}
            <span className="hidden sm:inline-flex items-center gap-1.5 rounded-full bg-stone-100 px-2.5 py-1 text-stone-600 border border-stone-200">
              <span className="h-1.5 w-1.5 rounded-full bg-stone-400" />
              {authUser.name || authUser.email.split('@')[0]}
            </span>

            {/* Logout */}
            <button
              onClick={() => { clearAuth(); setAuthUser(null) }}
              className="hidden sm:inline-flex items-center rounded-full bg-stone-100 px-2.5 py-1 text-stone-500 border border-stone-200 hover:bg-red-50 hover:border-red-200 hover:text-red-600 transition-colors"
              title="Se déconnecter"
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
              </svg>
            </button>

            {/* Tabs */}
            <div className="flex rounded-xl border border-stone-200 p-0.5 bg-stone-100">
              {([
                { id: 'analyse', label: 'Analyse' },
                { id: 'historique', label: 'Historique & Stats' },
                ...(authUser.role === 'admin' ? [{ id: 'admin', label: 'Admin' }] : []),
              ] as const).map(({ id, label }) => (
                <button
                  key={id}
                  onClick={() => setActiveTab(id as ActiveTab)}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-150 ${
                    activeTab === id
                      ? 'bg-white text-stone-900 shadow-warm-sm'
                      : 'text-stone-500 hover:text-stone-700'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </header>

      {/* Content */}
      <div className="mx-auto max-w-4xl px-4 py-10">

        {activeTab === 'historique' && (
          <HistoryDashboard onOpenJob={handleOpenJob} />
        )}

        {activeTab === 'admin' && authUser.role === 'admin' && (
          <AdminPanel />
        )}

        {activeTab === 'analyse' && phase === 'upload' && (
          <div className="mb-10 text-center">
            <div className="inline-flex items-center gap-1.5 rounded-full bg-sage-100 border border-sage-200 px-3 py-1 text-xs font-medium text-sage-700 mb-5">
              <span className="h-1.5 w-1.5 rounded-full bg-sage-500 animate-pulse" />
              Propulsé par Claude · IA éditoriale
            </div>
            <h2 className="text-3xl font-bold text-stone-900 leading-tight">
              Correction éditoriale<br className="hidden sm:block" /> exhaustive & intelligente
            </h2>
            <p className="mt-4 max-w-xl mx-auto text-stone-500 leading-relaxed">
              Déposez votre PDF et recevez-le annoté avec{' '}
              <span className="font-semibold text-stone-700">8 catégories de corrections</span> détectées par IA,
              de l'orthographe à la vérification des faits.
            </p>
            <div className="mt-6 flex flex-wrap justify-center gap-2">
              {[
                { label: 'A – Orthographe',     color: 'bg-red-50 border-red-200 text-red-700' },
                { label: 'B – Grammaire',        color: 'bg-orange-50 border-orange-200 text-orange-700' },
                { label: 'C – Typographie',      color: 'bg-purple-50 border-purple-200 text-purple-700' },
                { label: 'D – Style',            color: 'bg-blue-50 border-blue-200 text-blue-700' },
                { label: 'E – Sémantique',       color: 'bg-green-50 border-green-200 text-green-700' },
                { label: 'F – Uniformisation',   color: 'bg-cyan-50 border-cyan-200 text-cyan-700' },
                { label: 'G – Renvois',          color: 'bg-pink-50 border-pink-200 text-pink-700' },
                { label: 'H – Vérif. des faits', color: 'bg-amber-50 border-amber-200 text-amber-700' },
              ].map(({ label, color }) => (
                <span key={label} className={`rounded-full border px-3 py-1 text-[11px] font-medium shadow-warm-sm ${color}`}>
                  {label}
                </span>
              ))}
            </div>
          </div>
        )}

        {activeTab === 'analyse' && (phase === 'upload' || phase === 'estimating') && (
          <UploadZone onUpload={handleUpload} loading={uploading} />
        )}

        {activeTab === 'analyse' && (phase === 'processing' || phase === 'error') && job && (
          <div className="flex flex-col items-center gap-4">
            <ProgressTracker job={job} startedAt={startedAt} />
            {phase === 'error' && (
              <div className="flex gap-3 w-full max-w-xl">
                <button
                  onClick={handleRetry}
                  className="flex-1 rounded-xl bg-indigo-600 px-4 py-3 text-sm font-semibold text-white hover:bg-indigo-700 transition-colors flex items-center justify-center gap-2"
                >
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  Réessayer
                </button>
                <button
                  onClick={handleReset}
                  className="flex-1 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
                >
                  Nouvelle analyse
                </button>
              </div>
            )}
          </div>
        )}

        {activeTab === 'analyse' && phase === 'done' && job && (
          <ResultCard job={job} onNewDocument={handleReset} commentMode={commentMode} />
        )}
      </div>

      {phase === 'confirming' && estimate && (
        <CostEstimateModal
          estimate={estimate}
          onConfirm={handleConfirm}
          onCancel={handleCancel}
          confirming={confirming}
        />
      )}
    </main>
  )
}
