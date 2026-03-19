'use client'

import { useState, useEffect } from 'react'
import type { Correction, Category } from '@/lib/types'

const CATEGORY_CONFIG: Record<
  Category,
  { label: string; dot: string; badge: string; border: string }
> = {
  orthographe: {
    label: 'Orthographe',
    dot: 'bg-red-500',
    badge: 'bg-red-50 text-red-700 border border-red-200',
    border: 'border-l-red-400',
  },
  grammaire: {
    label: 'Grammaire',
    dot: 'bg-orange-500',
    badge: 'bg-orange-50 text-orange-700 border border-orange-200',
    border: 'border-l-orange-400',
  },
  typographie: {
    label: 'Typographie',
    dot: 'bg-blue-500',
    badge: 'bg-blue-50 text-blue-700 border border-blue-200',
    border: 'border-l-blue-400',
  },
  style: {
    label: 'Style',
    dot: 'bg-green-500',
    badge: 'bg-green-50 text-green-700 border border-green-200',
    border: 'border-l-green-400',
  },
}

const SEVERITY_LABELS: Record<string, { label: string; color: string }> = {
  error: { label: 'Erreur', color: 'text-red-600' },
  warning: { label: 'Attention', color: 'text-orange-500' },
  suggestion: { label: 'Suggestion', color: 'text-blue-500' },
}

interface Props {
  corrections: Correction[]
  selectedId: string | null
  onSelect: (id: string) => void
}

export function CorrectionPanel({ corrections, selectedId, onSelect }: Props) {
  const [filter, setFilter] = useState<Category | 'all'>('all')

  const counts = {
    orthographe: corrections.filter((c) => c.category === 'orthographe').length,
    grammaire: corrections.filter((c) => c.category === 'grammaire').length,
    typographie: corrections.filter((c) => c.category === 'typographie').length,
    style: corrections.filter((c) => c.category === 'style').length,
  }

  const filtered =
    filter === 'all' ? corrections : corrections.filter((c) => c.category === filter)

  // Scroller vers la correction sélectionnée dans le panel
  useEffect(() => {
    if (selectedId) {
      document.getElementById(`correction-${selectedId}`)?.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
      })
    }
  }, [selectedId])

  return (
    <div className="flex flex-col h-full">
      {/* En-tête */}
      <div className="px-4 py-3 border-b border-gray-200 bg-white sticky top-0 z-10">
        <h2 className="font-semibold text-gray-900 text-sm mb-2">
          {corrections.length} correction{corrections.length > 1 ? 's' : ''} détectée
          {corrections.length > 1 ? 's' : ''}
        </h2>

        {/* Légende */}
        <div className="flex flex-wrap gap-2 mb-3">
          {(Object.entries(CATEGORY_CONFIG) as [Category, (typeof CATEGORY_CONFIG)[Category]][]).map(
            ([cat, cfg]) => (
              <div key={cat} className="flex items-center gap-1">
                <div className={`w-2 h-2 rounded-full ${cfg.dot}`} />
                <span className="text-xs text-gray-500">
                  {cfg.label} ({counts[cat]})
                </span>
              </div>
            )
          )}
        </div>

        {/* Filtres */}
        <div className="flex flex-wrap gap-1">
          <FilterBtn
            label={`Tout (${corrections.length})`}
            active={filter === 'all'}
            onClick={() => setFilter('all')}
          />
          {(Object.entries(CATEGORY_CONFIG) as [Category, (typeof CATEGORY_CONFIG)[Category]][]).map(
            ([cat, cfg]) =>
              counts[cat] > 0 ? (
                <FilterBtn
                  key={cat}
                  label={`${cfg.label} (${counts[cat]})`}
                  active={filter === cat}
                  onClick={() => setFilter(cat)}
                />
              ) : null
          )}
        </div>
      </div>

      {/* Liste */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="p-8 text-center text-gray-400 text-sm">
            Aucune correction dans cette catégorie
          </div>
        ) : (
          filtered.map((correction) => {
            const cfg = CATEGORY_CONFIG[correction.category]
            const sev = SEVERITY_LABELS[correction.severity]
            const isSelected = correction.id === selectedId

            return (
              <div
                key={correction.id}
                id={`correction-${correction.id}`}
                className={`
                  px-4 py-3 border-b border-gray-100 cursor-pointer
                  transition-colors duration-100 border-l-4
                  ${cfg.border}
                  ${isSelected ? 'bg-gray-50' : 'hover:bg-gray-50/70'}
                `}
                onClick={() => onSelect(correction.id)}
              >
                {/* Badge catégorie + sévérité */}
                <div className="flex items-center gap-2 mb-1.5">
                  <span
                    className={`text-xs font-medium px-1.5 py-0.5 rounded-full ${cfg.badge}`}
                  >
                    {cfg.label}
                  </span>
                  <span className={`text-xs ${sev.color}`}>{sev.label}</span>
                </div>

                {/* Original → Corrigé */}
                <div className="flex items-center gap-1.5 mb-1.5 flex-wrap">
                  <span className="line-through text-red-500 text-sm font-mono bg-red-50 px-1 rounded">
                    {correction.snippet}
                  </span>
                  <span className="text-gray-400 text-xs">→</span>
                  <span className="text-green-700 font-semibold text-sm font-mono bg-green-50 px-1 rounded">
                    {correction.corrected}
                  </span>
                </div>

                {/* Règle */}
                <div className="text-sm font-semibold text-gray-800 mb-1">
                  {correction.rule}
                </div>

                {/* Explication */}
                <div className="text-xs text-gray-500 leading-relaxed">
                  {correction.explanation}
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

function FilterBtn({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`text-xs px-2 py-0.5 rounded-full font-medium transition-colors duration-100 ${
        active
          ? 'bg-gray-800 text-white'
          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
      }`}
    >
      {label}
    </button>
  )
}
