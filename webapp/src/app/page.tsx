'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import dynamic from 'next/dynamic'
import { UploadZone } from '@/components/UploadZone'
import { AnnotatedText } from '@/components/AnnotatedText'
import { CorrectionPanel } from '@/components/CorrectionPanel'
import { ExportButtons } from '@/components/ExportButtons'
import type { AnalysisResult, StreamEvent } from '@/lib/types'
import {
  loadCachedDoc,
  saveCachedDoc,
  updateCachedSession,
  loadLastDocMeta,
  fileCacheKey,
} from '@/lib/docCache'
import type { LastDocMeta } from '@/lib/docCache'
import { saveFile, loadFile } from '@/lib/fileCache'
import { extractPdfClientSide } from '@/lib/clientExtractor'

// Chargement dynamique (côté client uniquement) car react-pdf utilise le DOM/Worker
const PdfAnnotatedViewer = dynamic(
  () => import('@/components/PdfAnnotatedViewer').then((m) => m.PdfAnnotatedViewer),
  { ssr: false }
)

type Phase = 'upload' | 'loading' | 'results' | 'error'
type ViewMode = 'annotated' | 'pdf'

const CATEGORY_COLORS: Record<string, string> = {
  orthographe: 'text-red-600',
  grammaire: 'text-orange-500',
  typographie: 'text-blue-600',
  style: 'text-green-600',
  coherence: 'text-purple-600',
  renvoi: 'text-yellow-600',
}

function timeAgo(ts: number): string {
  const d = Math.floor((Date.now() - ts) / 1000)
  if (d < 60) return "à l'instant"
  if (d < 3600) return `il y a ${Math.floor(d / 60)} min`
  if (d < 86400) return `il y a ${Math.floor(d / 3600)} h`
  return `il y a ${Math.floor(d / 86400)} j`
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString('fr', { hour: '2-digit', minute: '2-digit' })
}

export default function Home() {
  const [phase, setPhase] = useState<Phase>('upload')
  const [status, setStatus] = useState('')
  const [progress, setProgress] = useState(0)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState('')
  const [filename, setFilename] = useState('')
  const [doneIds, setDoneIds] = useState<Set<string>>(new Set())
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<ViewMode>('annotated')
  const [lastMeta, setLastMeta] = useState<LastDocMeta | null>(null)
  const [fromCache, setFromCache] = useState(false)
  // Fichier restauré depuis IndexedDB pour la carte "Reprendre"
  const [storedFile, setStoredFile] = useState<File | null>(null)

  const currentFileRef = useRef<File | null>(null)

  // ── Au montage : charger la dernière session ──────────────────────────────
  useEffect(() => {
    const meta = loadLastDocMeta()
    setLastMeta(meta)
    if (meta) {
      // Essayer de charger le fichier depuis IndexedDB
      loadFile(meta.cacheKey).then((file) => {
        if (file) setStoredFile(file)
      })
    }
  }, [])

  // Sauvegarde automatique à chaque coche ou changement de sélection
  useEffect(() => {
    if (phase !== 'results' || !currentFileRef.current) return
    updateCachedSession(currentFileRef.current, Array.from(doneIds), selectedId)
    setSavedAt(Date.now())
  }, [doneIds, selectedId, phase])

  const handleToggleDone = useCallback((id: string) => {
    setDoneIds((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }, [])

  const handleFile = useCallback(
    async (file: File) => {
      // ── 1. Vérifier le cache — évite tout appel à Claude ──────────────────
      const cached = loadCachedDoc(file)
      if (cached) {
        currentFileRef.current = file
        setFilename(file.name)
        setResult(cached.result)
        setDoneIds(new Set(cached.doneIds))
        setSelectedId(cached.selectedId)
        setSavedAt(cached.savedAt)
        setFromCache(true)

        if (file.name.toLowerCase().endsWith('.pdf')) {
          setPdfUrl(URL.createObjectURL(file))
          setViewMode('pdf')
        } else {
          setPdfUrl(null)
          setViewMode('annotated')
        }
        // Sauvegarder le fichier en IndexedDB pour la prochaine fois
        saveFile(fileCacheKey(file), file)
        setPhase('results')
        return
      }

      // ── 2. Pas de cache — analyse complète ────────────────────────────────
      currentFileRef.current = file
      setFilename(file.name)
      setPhase('loading')
      setProgress(5)
      setStatus('Préparation…')
      setResult(null)
      setSelectedId(null)
      setDoneIds(new Set())
      setSavedAt(null)
      setFromCache(false)

      if (pdfUrl) {
        URL.revokeObjectURL(pdfUrl)
        setPdfUrl(null)
      }
      setViewMode('annotated')

      try {
        let fetchInit: RequestInit

        if (file.name.toLowerCase().endsWith('.pdf')) {
          // Extraction côté client — évite la limite 4.5MB de Vercel Hobby
          setStatus('Extraction du texte PDF…')
          setProgress(8)
          const extracted = await extractPdfClientSide(file, (page, total) => {
            setStatus(`Extraction page ${page}/${total}…`)
            setProgress(8 + Math.round((page / total) * 12))
          })
          fetchInit = {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: extracted.text, pageOffsets: extracted.pageOffsets }),
          }
        } else {
          const formData = new FormData()
          formData.append('file', file)
          fetchInit = { method: 'POST', body: formData }
        }

        const response = await fetch('/api/analyze', fetchInit)

        if (!response.ok) {
          const errText = await response.text().catch(() => '')
          throw new Error(`Erreur serveur ${response.status}${errText ? ' : ' + errText.slice(0, 200) : ''}`)
        }

        if (!response.body) throw new Error('Aucune réponse du serveur.')

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let gotResult = false

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() ?? ''

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            try {
              const event: StreamEvent = JSON.parse(line.slice(6))
              if (event.type === 'progress') {
                setStatus(event.message)
                setProgress(event.percent)
              } else if (event.type === 'result') {
                gotResult = true
                const r = event.data

                saveCachedDoc(file, r, [], null)
                // Sauvegarder le fichier dans IndexedDB
                saveFile(fileCacheKey(file), file)
                setSavedAt(Date.now())
                setLastMeta(loadLastDocMeta())
                setStoredFile(file)

                if (file.name.toLowerCase().endsWith('.pdf')) {
                  setPdfUrl(URL.createObjectURL(file))
                  setViewMode('pdf')
                }

                setResult(r)
                setProgress(100)
                setPhase('results')
              } else if (event.type === 'error') {
                gotResult = true
                setErrorMsg(event.message)
                setPhase('error')
              }
            } catch {
              // Ligne partielle
            }
          }
        }

        if (!gotResult) {
          throw new Error('Le serveur n\'a renvoyé aucun résultat. Le document est peut-être trop volumineux ou l\'analyse a expiré.')
        }
      } catch (err) {
        setErrorMsg(err instanceof Error ? err.message : 'Erreur réseau inattendue.')
        setPhase('error')
      }
    },
    [pdfUrl]
  )

  // Reprendre la dernière session sans re-uploader
  const handleResume = useCallback(() => {
    if (storedFile) handleFile(storedFile)
  }, [storedFile, handleFile])

  const handleSelect = useCallback((id: string) => {
    setSelectedId((prev) => (prev === id ? null : id))
  }, [])

  const reset = useCallback(() => {
    if (pdfUrl) URL.revokeObjectURL(pdfUrl)
    setPhase('upload')
    setResult(null)
    setSelectedId(null)
    setProgress(0)
    setStatus('')
    setErrorMsg('')
    setFilename('')
    setDoneIds(new Set())
    setSavedAt(null)
    setPdfUrl(null)
    setViewMode('annotated')
    setFromCache(false)
    currentFileRef.current = null
    setLastMeta(loadLastDocMeta())
  }, [pdfUrl])

  const canResume = lastMeta && lastMeta.total > 0 && storedFile !== null

  return (
    <div className="h-screen flex flex-col">
      {/* Header fixe */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between shadow-sm" style={{ height: '56px' }}>
        <div className="flex items-center gap-3">
          <span className="text-2xl select-none">✍️</span>
          <div>
            <h1 className="font-bold text-gray-900 text-lg leading-none">ÉditorIA</h1>
            <p className="text-gray-400 text-xs">Correcteur professionnel IA</p>
          </div>
        </div>

        <div className="hidden md:flex items-center gap-3 flex-wrap">
          {[
            { dot: 'bg-red-400', label: 'Orthographe' },
            { dot: 'bg-orange-400', label: 'Grammaire' },
            { dot: 'bg-blue-400', label: 'Typographie' },
            { dot: 'bg-green-400', label: 'Style' },
            { dot: 'bg-purple-400', label: 'Cohérence' },
            { dot: 'bg-yellow-400', label: 'Renvois' },
          ].map(({ dot, label }) => (
            <div key={label} className="flex items-center gap-1.5">
              <div className={`w-2 h-2 rounded-full ${dot}`} />
              <span className="text-xs text-gray-500">{label}</span>
            </div>
          ))}
        </div>

        {phase !== 'upload' && (
          <button
            onClick={reset}
            className="text-sm text-gray-500 hover:text-gray-800 transition-colors border border-gray-200 px-3 py-1.5 rounded-lg hover:border-gray-400"
          >
            ← Nouveau document
          </button>
        )}
      </header>

      {/* Espaceur pour le header fixe */}
      <div style={{ height: '56px', flexShrink: 0 }} />

      {/* ─── Upload ─── */}
      {phase === 'upload' && (
        <main className="flex-1 flex flex-col items-center justify-center p-8 max-w-2xl mx-auto w-full overflow-y-auto">
          <div className="text-center mb-8">
            <h2 className="text-3xl font-bold text-gray-900 mb-3">Corrigez votre document</h2>
            <p className="text-gray-500 text-base">
              Déposez un fichier Word ou PDF pour obtenir une analyse complète : orthographe,
              grammaire, typographie et style.
            </p>
          </div>

          {/* Carte dernier projet */}
          {lastMeta && lastMeta.total > 0 && (
            <div className="w-full mb-5 bg-white border border-blue-100 rounded-xl p-4 shadow-sm">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-semibold text-blue-600 uppercase tracking-wide mb-1">
                    Dernier projet
                  </p>
                  <p className="text-sm font-medium text-gray-800 truncate">
                    📄 {lastMeta.filename}
                  </p>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {lastMeta.doneCount}/{lastMeta.total} corrections faites
                    {' · '}
                    {timeAgo(lastMeta.savedAt)}
                  </p>

                  {/* Barre de progression */}
                  <div className="mt-2 w-40 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-green-400 rounded-full"
                      style={{
                        width: `${Math.round((lastMeta.doneCount / lastMeta.total) * 100)}%`,
                      }}
                    />
                  </div>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {Math.round((lastMeta.doneCount / lastMeta.total) * 100)}% fait
                  </p>
                </div>

                <div className="flex-shrink-0 flex flex-col gap-2 items-end">
                  {/* Bouton Reprendre (si fichier disponible) */}
                  {canResume ? (
                    <button
                      onClick={handleResume}
                      className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors shadow-sm"
                    >
                      ▶ Reprendre
                    </button>
                  ) : (
                    <p className="text-xs text-gray-400 text-right max-w-[160px]">
                      Rechargez le même fichier pour reprendre
                      <br />
                      <span className="text-green-600 font-medium">sans relancer l&apos;analyse</span>
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          <div className="w-full">
            <UploadZone onFile={handleFile} />
          </div>

          <div className="mt-8 grid grid-cols-2 md:grid-cols-4 gap-4 w-full">
            {[
              { emoji: '🔴', title: 'Orthographe', desc: 'Fautes, homophones, accents' },
              { emoji: '🟠', title: 'Grammaire', desc: 'Accords, conjugaison, syntaxe' },
              { emoji: '🔵', title: 'Typographie', desc: 'Ponctuation, guillemets, espaces' },
              { emoji: '🟢', title: 'Style', desc: 'Répétitions, anglicismes, rythme' },
            ].map(({ emoji, title, desc }) => (
              <div
                key={title}
                className="bg-white rounded-xl p-4 border border-gray-100 shadow-sm text-center"
              >
                <div className="text-2xl mb-2">{emoji}</div>
                <div className="font-semibold text-gray-800 text-sm mb-1">{title}</div>
                <div className="text-xs text-gray-400">{desc}</div>
              </div>
            ))}
          </div>

          <p className="mt-6 text-xs text-gray-400 text-center">
            🔒 Votre document est traité à la volée et n&apos;est jamais stocké sur nos serveurs.
          </p>
        </main>
      )}

      {/* ─── Loading ─── */}
      {phase === 'loading' && (
        <main className="flex-1 flex flex-col items-center justify-center p-8">
          <div className="w-full max-w-md">
            <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-8 text-center">
              <div className="relative w-16 h-16 mx-auto mb-6">
                <div className="absolute inset-0 rounded-full border-4 border-gray-100" />
                <div className="absolute inset-0 rounded-full border-4 border-t-blue-500 animate-spin" />
              </div>
              <h3 className="font-semibold text-gray-900 mb-1">{filename}</h3>
              <p className="text-gray-500 text-sm mb-6">{status}</p>
              <div className="w-full bg-gray-100 rounded-full h-2 overflow-hidden">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-blue-500 to-blue-600 transition-all duration-500"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <p className="text-xs text-gray-400 mt-2">{progress}%</p>
              <p className="text-xs text-gray-400 mt-6">
                L&apos;analyse peut prendre 30 à 60 secondes selon la longueur du document.
              </p>
            </div>
          </div>
        </main>
      )}

      {/* ─── Error ─── */}
      {phase === 'error' && (
        <main className="flex-1 flex flex-col items-center justify-center p-8">
          <div className="bg-white rounded-2xl shadow-sm border border-red-100 p-8 max-w-md w-full text-center">
            <div className="text-4xl mb-4">⚠️</div>
            <h3 className="font-semibold text-gray-900 mb-2">Erreur lors de l&apos;analyse</h3>
            <p className="text-red-600 text-sm mb-6 bg-red-50 p-3 rounded-lg">{errorMsg}</p>
            <button
              onClick={reset}
              className="px-6 py-2 bg-gray-800 text-white rounded-lg text-sm hover:bg-gray-700 transition-colors"
            >
              Réessayer
            </button>
          </div>
        </main>
      )}

      {/* ─── Results ─── */}
      {phase === 'results' && result && (
        <main className="flex-1 flex flex-col overflow-hidden min-h-0">
          {/* Barre de stats */}
          <div className="bg-white border-b border-gray-200 px-4 py-2 flex items-center gap-3 flex-wrap text-sm">
            <span className="text-gray-500 font-medium truncate max-w-[200px]">
              📄 {filename}
            </span>
            <span className="text-gray-400">{result.wordCount.toLocaleString('fr')} mots</span>
            {(['orthographe', 'grammaire', 'typographie', 'style', 'coherence', 'renvoi'] as const).map((cat) => {
              const count = result.corrections.filter((c) => c.category === cat).length
              return count > 0 ? (
                <span key={cat} className={`font-medium capitalize ${CATEGORY_COLORS[cat]}`}>
                  {count} {cat}
                </span>
              ) : null
            })}

            {/* Toggle vue PDF annoté / texte annoté */}
            {pdfUrl && (
              <div className="flex items-center gap-1 ml-2 bg-gray-100 rounded-lg p-0.5">
                <button
                  onClick={() => setViewMode('annotated')}
                  className={`text-xs px-2.5 py-1 rounded-md transition-colors ${
                    viewMode === 'annotated'
                      ? 'bg-white shadow-sm text-gray-800 font-medium'
                      : 'text-gray-500 hover:text-gray-700'
                  }`}
                >
                  ✏️ Texte annoté
                </button>
                <button
                  onClick={() => setViewMode('pdf')}
                  className={`text-xs px-2.5 py-1 rounded-md transition-colors ${
                    viewMode === 'pdf'
                      ? 'bg-white shadow-sm text-gray-800 font-medium'
                      : 'text-gray-500 hover:text-gray-700'
                  }`}
                >
                  📄 PDF annoté
                </button>
              </div>
            )}

            {/* Indicateur de sauvegarde */}
            <span className="ml-auto flex items-center gap-1 text-xs text-gray-400">
              {fromCache && (
                <span className="text-blue-500 font-medium mr-2">↩ Restauré du cache</span>
              )}
              <SaveIcon />
              {savedAt ? `Sauvegardé à ${formatTime(savedAt)}` : 'Non sauvegardé'}
            </span>
          </div>

          {/* Split view */}
          <div className="flex-1 flex overflow-hidden min-h-0">
            {viewMode === 'pdf' && pdfUrl ? (
              /* Mode PDF : visionneuse pleine largeur avec panneau corrections intégré */
              <div className="flex-1 overflow-hidden min-h-0">
                <PdfAnnotatedViewer
                  pdfUrl={pdfUrl}
                  corrections={result.corrections}
                  selectedId={selectedId}
                  doneIds={doneIds}
                  onSelect={handleSelect}
                  onToggleDone={handleToggleDone}
                />
              </div>
            ) : (
              /* Mode texte annoté : texte + panneau corrections séparé */
              <>
                <div className="flex-1 overflow-hidden min-h-0">
                  <div className="h-full overflow-y-auto p-6 lg:p-10">
                    <AnnotatedText
                      text={result.extractedText}
                      formattedHtml={result.formattedHtml}
                      pageOffsets={result.pageOffsets}
                      corrections={result.corrections}
                      selectedId={selectedId}
                      onSelect={handleSelect}
                    />
                  </div>
                </div>

                <aside className="w-80 xl:w-96 border-l border-gray-200 bg-white overflow-y-auto flex-shrink-0">
                  <CorrectionPanel
                    corrections={result.corrections}
                    selectedId={selectedId}
                    onSelect={handleSelect}
                    doneIds={doneIds}
                    onToggleDone={handleToggleDone}
                  />
                </aside>
              </>
            )}
          </div>

          {/* Barre d'export */}
          <div className="bg-white border-t border-gray-200 px-6 py-3 flex items-center gap-4 flex-wrap">
            <span className="text-sm text-gray-500 font-medium">Exporter :</span>
            <ExportButtons result={result} filename={filename} />
          </div>
        </main>
      )}
    </div>
  )
}

function SaveIcon() {
  return (
    <svg
      className="w-3.5 h-3.5"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <path d="M13 2H3a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V5l-3-3z" />
      <path d="M11 2v3H5V2" />
      <path d="M5 9h6M5 12h4" />
    </svg>
  )
}
