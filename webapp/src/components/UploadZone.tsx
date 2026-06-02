'use client'

import { useCallback, useState } from 'react'
import type { DocType } from '@/lib/types'
import { DOC_TYPE_META } from '@/lib/types'

interface Props {
  onUpload: (file: File, docType: DocType) => void
  loading: boolean
}

const DOC_TYPE_GROUPS: { label: string; types: DocType[] }[] = [
  { label: 'Littérature', types: ['roman', 'bd_comics', 'jeunesse', 'poesie_theatre'] },
  { label: 'Non-fiction',  types: ['documentaire', 'beaux_arts', 'tourisme', 'cuisine', 'sport'] },
  { label: 'Éducatif',    types: ['manuel_scolaire', 'parascolaire'] },
  { label: 'Presse',      types: ['magazine', 'revue_presse'] },
  { label: 'Autre',       types: ['essai', 'autre'] },
]

export function UploadZone({ onUpload, loading }: Props) {
  const [dragging, setDragging] = useState(false)
  const [docType, setDocType] = useState<DocType>('roman')

  const handleFile = useCallback(
    (file: File) => {
      if (!file.name.toLowerCase().endsWith('.pdf')) {
        alert('Veuillez sélectionner un fichier PDF.')
        return
      }
      onUpload(file, docType)
    },
    [onUpload, docType]
  )

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setDragging(false)
      const file = e.dataTransfer.files[0]
      if (file) handleFile(file)
    },
    [handleFile]
  )

  const onInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file) handleFile(file)
    },
    [handleFile]
  )

  return (
    <div className="space-y-6">

      {/* ── Étape 1 : Type de document ──────────────────────────────────── */}
      <div className="rounded-2xl bg-white border border-stone-200 shadow-warm-sm overflow-hidden">
        <div className="px-5 py-3.5 border-b border-stone-100 flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-sage-600 text-white text-xs font-bold shrink-0">1</span>
          <p className="text-sm font-semibold text-stone-800">Type de document</p>
          <p className="text-xs text-stone-400 ml-1">— améliore la précision de la correction</p>
        </div>
        <div className="px-5 py-4 space-y-4">
          {DOC_TYPE_GROUPS.map((group) => (
            <div key={group.label}>
              <p className="text-[11px] font-semibold uppercase tracking-widest text-stone-400 mb-2">
                {group.label}
              </p>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {group.types.map((type) => {
                  const meta = DOC_TYPE_META[type]
                  const selected = docType === type
                  return (
                    <button
                      key={type}
                      type="button"
                      disabled={loading}
                      onClick={() => setDocType(type)}
                      className={[
                        'flex flex-col items-center gap-1.5 rounded-xl border-2 px-2 py-3 text-center',
                        'transition-all duration-150 cursor-pointer select-none',
                        selected
                          ? 'border-sage-500 bg-sage-50 shadow-warm-sm -translate-y-px'
                          : 'border-stone-200 bg-white hover:border-sage-300 hover:bg-sage-50/50 hover:-translate-y-px hover:shadow-warm-sm',
                        loading ? 'opacity-60 cursor-not-allowed translate-y-0 shadow-none' : '',
                      ].join(' ')}
                    >
                      <span className="text-2xl">{meta.icon}</span>
                      <span className={`font-semibold leading-tight text-xs ${selected ? 'text-sage-800' : 'text-stone-700'}`}>
                        {meta.label}
                      </span>
                      <span className="text-[10px] text-stone-400 leading-tight hidden sm:block">{meta.description}</span>
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Étape 2 : Zone de dépôt ─────────────────────────────────────── */}
      <div className="rounded-2xl bg-white border border-stone-200 shadow-warm-sm overflow-hidden">
        <div className="px-5 py-3.5 border-b border-stone-100 flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-sage-600 text-white text-xs font-bold shrink-0">2</span>
          <p className="text-sm font-semibold text-stone-800">Déposez votre PDF</p>
        </div>
        <div className="p-5">
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={[
              'relative flex flex-col items-center justify-center gap-4',
              'rounded-xl border-2 border-dashed p-12 transition-all duration-200',
              dragging
                ? 'border-sage-400 bg-sage-50'
                : 'border-stone-300 bg-stone-50 hover:border-sage-300 hover:bg-sage-50/60',
              loading ? 'opacity-60 pointer-events-none' : 'cursor-pointer',
            ].join(' ')}
          >
            <input
              type="file"
              accept=".pdf"
              onChange={onInputChange}
              disabled={loading}
              className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
            />

            {/* Icon */}
            <div className={`flex h-16 w-16 items-center justify-center rounded-2xl transition-colors ${dragging ? 'bg-sage-200' : 'bg-stone-200'}`}>
              {loading ? (
                <svg className="h-8 w-8 text-sage-500 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
              ) : (
                <svg className={`h-8 w-8 transition-colors ${dragging ? 'text-sage-600' : 'text-stone-500'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                </svg>
              )}
            </div>

            <div className="text-center">
              <p className="text-base font-semibold text-stone-700">
                {loading ? 'Envoi en cours…' : 'Glissez votre PDF ici'}
              </p>
              <p className="mt-1 text-sm text-stone-500">
                ou{' '}
                <span className={`font-semibold transition-colors ${dragging ? 'text-sage-600' : 'text-sage-600 hover:text-sage-700'}`}>
                  cliquez pour parcourir
                </span>
              </p>
              <p className="mt-2 text-xs text-stone-400">PDF uniquement · Max 50 Mo</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
