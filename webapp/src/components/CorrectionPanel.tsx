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
  coherence: {
    label: 'Cohérence',
    dot: 'bg-purple-500',
    badge: 'bg-purple-50 text-purple-700 border border-purple-200',
    border: 'border-l-purple-400',
  },
  renvoi: {
    label: 'Renvoi',
    dot: 'bg-yellow-500',
    badge: 'bg-yellow-50 text-yellow-700 border border-yellow-200',
    border: 'border-l-yellow-400',
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
  doneIds: Set<string>
  onToggleDone: (id: string) => void
}

export function CorrectionPanel({
  corrections,
  selectedId,
  onSelect,
  doneIds,
  onToggleDone,
}: Props) {
  const [filter, setFilter] = useState<Category | 'all'>('all')
  const [hideDone, setHideDone] = useState(false)

  const counts = {
    orthographe: corrections.filter((c) => c.category === 'orthographe').length,
    grammaire: corrections.filter((c) => c.category === 'grammaire').length,
    typographie: corrections.filter((c) => c.category === 'typographie').length,
    style: corrections.filter((c) => c.category === 'style').length,
    coherence: corrections.filter((c) => c.category === 'coherence').length,
    renvoi: corrections.filter((c) => c.category === 'renvoi').length,
  }

  const doneCount = doneIds.size
  const remaining = corrections.length - doneCount

  const filtered = corrections
    .filter((c) => filter === 'all' || c.category === filter)
    .filter((c) => !hideDone || !doneIds.has(c.id))

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
        {/* Compteurs */}
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-semibold text-gray-900 text-sm">
            {corrections.length} correction{corrections.length > 1 ? 's' : ''}
          </h2>
          {doneCount > 0 && (
            <span className="text-xs text-green-600 font-medium flex items-center gap-1">
              <CheckIcon className="w-3.5 h-3.5" />
              {doneCount} faite{doneCount > 1 ? 's' : ''}
              {remaining > 0 && (
                <span className="text-gray-400 font-normal">· {remaining} restante{remaining > 1 ? 's' : ''}</span>
              )}
            </span>
          )}
        </div>

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
          {doneCount > 0 && (
            <FilterBtn
              label={hideDone ? `Afficher faites (${doneCount})` : `Masquer faites`}
              active={hideDone}
              onClick={() => setHideDone((v) => !v)}
            />
          )}
        </div>
      </div>

      {/* Liste groupée par page */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="p-8 text-center text-gray-400 text-sm">
            {hideDone && doneCount > 0
              ? 'Toutes les corrections visibles sont faites ✓'
              : 'Aucune correction dans cette catégorie'}
          </div>
        ) : (
          <GroupedList
            corrections={filtered}
            selectedId={selectedId}
            doneIds={doneIds}
            onSelect={onSelect}
            onToggleDone={onToggleDone}
          />
        )}
      </div>
    </div>
  )
}

function GroupedList({
  corrections,
  selectedId,
  doneIds,
  onSelect,
  onToggleDone,
}: {
  corrections: Correction[]
  selectedId: string | null
  doneIds: Set<string>
  onSelect: (id: string) => void
  onToggleDone: (id: string) => void
}) {
  // Grouper par page (undefined → pas de page)
  const hasPaged = corrections.some((c) => c.pageNum !== undefined)

  if (!hasPaged) {
    return (
      <>
        {corrections.map((c) => (
          <CorrectionCard
            key={c.id}
            correction={c}
            isSelected={c.id === selectedId}
            isDone={doneIds.has(c.id)}
            onSelect={onSelect}
            onToggleDone={onToggleDone}
          />
        ))}
      </>
    )
  }

  // Construire les groupes
  const groups = new Map<number | 'none', Correction[]>()
  for (const c of corrections) {
    const key = c.pageNum ?? 'none'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(c)
  }
  const sortedKeys = Array.from(groups.keys()).sort((a, b) => {
    if (a === 'none') return 1
    if (b === 'none') return -1
    return (a as number) - (b as number)
  })

  return (
    <>
      {sortedKeys.map((key) => (
        <div key={String(key)}>
          <div className="px-4 py-1.5 bg-gray-50 border-b border-gray-200 sticky top-0 z-[5]">
            <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
              {key === 'none' ? 'Document' : `Page ${key}`}
            </span>
          </div>
          {groups.get(key)!.map((c) => (
            <CorrectionCard
              key={c.id}
              correction={c}
              isSelected={c.id === selectedId}
              isDone={doneIds.has(c.id)}
              onSelect={onSelect}
              onToggleDone={onToggleDone}
            />
          ))}
        </div>
      ))}
    </>
  )
}

function CorrectionCard({
  correction,
  isSelected,
  isDone,
  onSelect,
  onToggleDone,
}: {
  correction: Correction
  isSelected: boolean
  isDone: boolean
  onSelect: (id: string) => void
  onToggleDone: (id: string) => void
}) {
  const cfg = CATEGORY_CONFIG[correction.category]
  const sev = SEVERITY_LABELS[correction.severity]
  return (
    <div
      id={`correction-${correction.id}`}
      className={`
        px-4 py-3 border-b border-gray-100 cursor-pointer
        transition-colors duration-100 border-l-4
        ${cfg.border}
        ${isDone ? 'opacity-50' : ''}
        ${isSelected ? 'bg-gray-50' : 'hover:bg-gray-50/70'}
      `}
      onClick={() => onSelect(correction.id)}
    >
      {/* Badge catégorie + sévérité + coche */}
      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
        <span className={`text-xs font-medium px-1.5 py-0.5 rounded-full ${cfg.badge}`}>
          {cfg.label}
        </span>
        <span className={`text-xs ${sev.color}`}>{sev.label}</span>
        <button
          className={`ml-auto flex items-center justify-center w-5 h-5 rounded-full border transition-colors duration-100 ${
            isDone
              ? 'bg-green-500 border-green-500 text-white'
              : 'border-gray-300 text-transparent hover:border-green-400 hover:text-green-400'
          }`}
          title={isDone ? 'Marquer comme non faite' : 'Marquer comme faite'}
          onClick={(e) => { e.stopPropagation(); onToggleDone(correction.id) }}
        >
          <CheckIcon className="w-3 h-3" />
        </button>
      </div>

      {/* Original → Corrigé */}
      <div className={`flex items-center gap-1.5 mb-1.5 flex-wrap ${isDone ? 'line-through' : ''}`}>
        <span className="line-through text-red-500 text-sm font-mono bg-red-50 px-1 rounded">
          {correction.snippet}
        </span>
        <span className="text-gray-400 text-xs">→</span>
        <span className="text-green-700 font-semibold text-sm font-mono bg-green-50 px-1 rounded">
          {correction.corrected}
        </span>
      </div>

      {/* Règle */}
      <div className="text-sm font-semibold text-gray-800 mb-1">{correction.rule}</div>

      {/* Explication */}
      <div className="text-xs text-gray-500 leading-relaxed">{correction.explanation}</div>
    </div>
  )
}

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="2,6 5,9 10,3" />
    </svg>
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
        active ? 'bg-gray-800 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
      }`}
    >
      {label}
    </button>
  )
}
