'use client'

import { useState, useCallback, useEffect } from 'react'
import { UploadZone } from '@/components/UploadZone'
import { AnnotatedText } from '@/components/AnnotatedText'
import { CorrectionPanel } from '@/components/CorrectionPanel'
import { ExportButtons } from '@/components/ExportButtons'
import type { AnalysisResult, StreamEvent } from '@/lib/types'

type Phase = 'upload' | 'loading' | 'results' | 'error'

const CATEGORY_COLORS: Record<string, string> = {
  orthographe: 'text-red-600',
  grammaire: 'text-orange-500',
  typographie: 'text-blue-600',
  style: 'text-green-600',
}

export default function Home() {
  const [phase, setPhase] = useState<Phase>('upload')
  const [status, setStatus] = useState('')
  const [progress, setProgress] = useState(0)
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState('')
  const [filename, setFilename] = useState('')

  const handleFile = useCallback(async (file: File) => {
    setFilename(file.name)
    setPhase('loading')
    setProgress(5)
    setStatus('Préparation…')
    setResult(null)
    setSelectedId(null)

    try {
      const formData = new FormData()
      formData.append('file', file)

      const response = await fetch('/api/analyze', { method: 'POST', body: formData })

      if (!response.body) throw new Error('Aucune réponse du serveur.')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

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
              setResult(event.data)
              setProgress(100)
              setPhase('results')
            } else if (event.type === 'error') {
              setErrorMsg(event.message)
              setPhase('error')
            }
          } catch {
            // Ligne partielle ou non-JSON, ignorer
          }
        }
      }
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Erreur réseau inattendue.')
      setPhase('error')
    }
  }, [])

  // Synchroniser la sélection dans le texte et dans le panel
  const handleSelect = useCallback((id: string) => {
    setSelectedId((prev) => (prev === id ? null : id))
  }, [])

  const reset = useCallback(() => {
    setPhase('upload')
    setResult(null)
    setSelectedId(null)
    setProgress(0)
    setStatus('')
    setErrorMsg('')
    setFilename('')
  }, [])

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between sticky top-0 z-20 shadow-sm">
        <div className="flex items-center gap-3">
          <span className="text-2xl select-none">✍️</span>
          <div>
            <h1 className="font-bold text-gray-900 text-lg leading-none">ÉditorIA</h1>
            <p className="text-gray-400 text-xs">Correcteur professionnel IA</p>
          </div>
        </div>

        {/* Légende catégories */}
        <div className="hidden md:flex items-center gap-4">
          {[
            { key: 'orthographe', dot: 'bg-red-400', label: 'Orthographe' },
            { key: 'grammaire', dot: 'bg-orange-400', label: 'Grammaire' },
            { key: 'typographie', dot: 'bg-blue-400', label: 'Typographie' },
            { key: 'style', dot: 'bg-green-400', label: 'Style' },
          ].map(({ dot, label }) => (
            <div key={label} className="flex items-center gap-1.5">
              <div className={`w-2.5 h-2.5 rounded-full ${dot}`} />
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

      {/* ─── Phase Upload ─── */}
      {phase === 'upload' && (
        <main className="flex-1 flex flex-col items-center justify-center p-8 max-w-2xl mx-auto w-full">
          <div className="text-center mb-8">
            <h2 className="text-3xl font-bold text-gray-900 mb-3">
              Corrigez votre document
            </h2>
            <p className="text-gray-500 text-base">
              Déposez un fichier Word ou PDF pour obtenir une analyse complète :
              orthographe, grammaire, typographie et style — avec la règle pour chaque
              correction.
            </p>
          </div>

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
            🔒 Votre document est traité à la volée et n&apos;est jamais stocké.
            Nécessite une clé API Anthropic (variable ANTHROPIC_API_KEY).
          </p>
        </main>
      )}

      {/* ─── Phase Loading ─── */}
      {phase === 'loading' && (
        <main className="flex-1 flex flex-col items-center justify-center p-8">
          <div className="w-full max-w-md">
            <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-8 text-center">
              {/* Spinner */}
              <div className="relative w-16 h-16 mx-auto mb-6">
                <div className="absolute inset-0 rounded-full border-4 border-gray-100" />
                <div className="absolute inset-0 rounded-full border-4 border-t-blue-500 animate-spin" />
              </div>

              <h3 className="font-semibold text-gray-900 mb-1">{filename}</h3>
              <p className="text-gray-500 text-sm mb-6">{status}</p>

              {/* Barre de progression */}
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

      {/* ─── Phase Error ─── */}
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

      {/* ─── Phase Results ─── */}
      {phase === 'results' && result && (
        <main className="flex-1 flex flex-col overflow-hidden">
          {/* Barre de stats */}
          <div className="bg-white border-b border-gray-200 px-6 py-2.5 flex items-center gap-6 flex-wrap">
            <span className="text-sm text-gray-500 font-medium truncate max-w-xs">
              📄 {filename}
            </span>
            <span className="text-sm text-gray-400">
              {result.wordCount.toLocaleString('fr')} mots
            </span>
            {(['orthographe', 'grammaire', 'typographie', 'style'] as const).map((cat) => {
              const count = result.corrections.filter((c) => c.category === cat).length
              return count > 0 ? (
                <span
                  key={cat}
                  className={`text-sm font-medium capitalize ${CATEGORY_COLORS[cat]}`}
                >
                  {count} {cat}
                </span>
              ) : null
            })}
          </div>

          {/* Split view */}
          <div className="flex-1 flex overflow-hidden">
            {/* Texte annoté */}
            <div className="flex-1 overflow-y-auto p-6 lg:p-10">
              <AnnotatedText
                text={result.extractedText}
                corrections={result.corrections}
                selectedId={selectedId}
                onSelect={handleSelect}
              />
            </div>

            {/* Panel latéral */}
            <aside className="w-80 xl:w-96 border-l border-gray-200 bg-white overflow-y-auto flex-shrink-0">
              <CorrectionPanel
                corrections={result.corrections}
                selectedId={selectedId}
                onSelect={handleSelect}
              />
            </aside>
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
