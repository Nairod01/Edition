'use client'

import { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import type { JobState, Correction, CommentMode } from '@/lib/types'
import { CATEGORY_META } from '@/lib/types'
import { FeedbackModal } from '@/components/FeedbackModal'
import { apiFetch } from '@/lib/auth'

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'

const _CONF_RANK: Record<string, number> = { 'Certain': 0, 'Probable': 1, 'À vérifier': 2 }

const CONFIDENCE_COLOR: Record<string, string> = {
  'Certain': 'bg-green-100 text-green-700',
  'Probable': 'bg-amber-100 text-amber-700',
  'À vérifier': 'bg-red-100 text-red-700',
}

// Couleurs de surligneur par catégorie — semi-transparent pour overlay PDF
const CATEGORY_BBOX_COLOR: Record<string, string> = {
  A: 'rgba(239,68,68,0.22)',
  B: 'rgba(249,115,22,0.22)',
  C: 'rgba(139,92,246,0.22)',
  D: 'rgba(59,130,246,0.22)',
  E: 'rgba(34,197,94,0.22)',
  F: 'rgba(6,182,212,0.22)',
  G: 'rgba(236,72,153,0.22)',
  H: 'rgba(245,158,11,0.22)',
}

const CATEGORY_BBOX_COLOR_BRIGHT: Record<string, string> = {
  A: 'rgba(239,68,68,0.48)',
  B: 'rgba(249,115,22,0.48)',
  C: 'rgba(139,92,246,0.48)',
  D: 'rgba(59,130,246,0.48)',
  E: 'rgba(34,197,94,0.48)',
  F: 'rgba(6,182,212,0.48)',
  G: 'rgba(236,72,153,0.48)',
  H: 'rgba(245,158,11,0.48)',
}

const CATEGORY_BORDER_COLOR: Record<string, string> = {
  A: '#ef4444',
  B: '#f97316',
  C: '#8b5cf6',
  D: '#3b82f6',
  E: '#22c55e',
  F: '#06b6d4',
  G: '#ec4899',
  H: '#f59e0b',
}

interface Props {
  job: JobState
  onNewDocument: () => void
  commentMode?: CommentMode
}

export function ResultCard({ job, onNewDocument, commentMode = 'detailed' }: Props) {
  // ── Downloads ──────────────────────────────────────────────────────────────
  const [downloading, setDownloading] = useState(false)
  const [downloadingDocx, setDownloadingDocx] = useState(false)
  // ── Corrections list ───────────────────────────────────────────────────────
  const [showCorrections, setShowCorrections] = useState(false)
  const [corrections, setCorrections] = useState<Correction[]>([])
  const [loadingCorr, setLoadingCorr] = useState(false)
  const [filterCat, setFilterCat] = useState<string>('all')
  const [sortBy, setSortBy] = useState<'page' | 'confidence'>('page')
  const [filterPin, setFilterPin] = useState(false)
  const [filterLike, setFilterLike] = useState(false)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [pinnedIds, setPinnedIds] = useState<Set<string>>(new Set())
  const [likedIds, setLikedIds] = useState<Set<string>>(new Set())
  const [flashKey, setFlashKey] = useState(0)
  const [showUnlocated, setShowUnlocated] = useState(false)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const [pinFlashedId, setPinFlashedId] = useState<string | null>(null)
  // ── Search ─────────────────────────────────────────────────────────────────
  const [searchQuery, setSearchQuery] = useState('')
  // ── Feedback ───────────────────────────────────────────────────────────────
  const [rejectedIds, setRejectedIds] = useState<Set<string>>(new Set())
  const [feedbackTargetCorr, setFeedbackTargetCorr] = useState<Correction | null>(null)
  // ── Multi-selection ────────────────────────────────────────────────────────
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [bulkFpLoading, setBulkFpLoading] = useState(false)
  // ── Split view ─────────────────────────────────────────────────────────────
  const [activeCorrId, setActiveCorrId] = useState<string | null>(null)
  // pageImageCache: pageNum (0-indexed) → '' (loading) | blob URL | undefined (not started/error)
  const [pageImageCache, setPageImageCache] = useState<Record<number, string>>({})

  // ── Scroll sync refs ───────────────────────────────────────────────────────
  const correctionsContainerRef = useRef<HTMLDivElement>(null)
  const pdfScrollRef = useRef<HTMLDivElement>(null)
  const scrollDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const displayedCorrectionsRef = useRef<Correction[]>([])
  const activeCorrIdRef = useRef<string | null>(null)

  useEffect(() => { activeCorrIdRef.current = activeCorrId }, [activeCorrId])


  // ── Thumbnail sidebar ──────────────────────────────────────────────────────
  const [showThumbnails, setShowThumbnails] = useState(true)

  // ── Page dimensions & zoom ─────────────────────────────────────────────────
  const [pageDimensions, setPageDimensions] = useState<Record<number, { w: number; h: number }>>({})
  const [zoomScale, setZoomScale] = useState(1.0)
  // ── Manual page navigation (independent of correction selection) ───────────
  const [viewPageNum, setViewPageNum] = useState<number | null>(null)

  // ── Add correction form ────────────────────────────────────────────────────
  const [addCorrFormOpen, setAddCorrFormOpen] = useState(false)
  const [addCorrPage, setAddCorrPage] = useState(1)
  const [addCorrData, setAddCorrData] = useState({ category: 'A', original_text: '', corrected_text: '', description: '' })
  const [addCorrLoading, setAddCorrLoading] = useState(false)

  // ── Page image loader ──────────────────────────────────────────────────────
  const loadPageIfNeeded = useCallback((pageNum: number) => {
    setPageImageCache(prev => {
      if (prev[pageNum] !== undefined) return prev
      apiFetch(`${BACKEND_URL}/api/jobs/${job.id}/pages/${pageNum}/preview`)
        .then(async res => {
          if (!res.ok) throw new Error()
          const w = parseFloat(res.headers.get('X-Page-Width-Pts') || '0')
          const h = parseFloat(res.headers.get('X-Page-Height-Pts') || '0')
          if (w > 0 && h > 0) {
            setPageDimensions(p => ({ ...p, [pageNum]: { w, h } }))
          }
          const blob = await res.blob()
          const url = URL.createObjectURL(blob)
          setPageImageCache(p => ({ ...p, [pageNum]: url }))
        })
        .catch(() => {
          setPageImageCache(p => { const n = { ...p }; delete n[pageNum]; return n })
        })
      return { ...prev, [pageNum]: '' }
    })
  }, [job.id])

  // ── Scroll PDF viewer to active bbox ──────────────────────────────────────
  const scrollToBbox = useCallback(() => {
    const corrId = activeCorrIdRef.current
    if (!corrId || !pdfScrollRef.current) return
    const corr = corrections.find(c => c.id === corrId)
    if (!corr?.bbox) return
    const pageNum = corr.page - 1
    const dims = pageDimensions[pageNum]
    if (!dims) return
    const container = pdfScrollRef.current
    const img = container.querySelector('img') as HTMLImageElement | null
    if (!img || !img.complete || img.naturalWidth === 0) return
    const imgHeight = img.offsetHeight
    const paddingTop = 32 // py-8
    const bboxCenterPx = paddingTop + ((corr.bbox.y0 + corr.bbox.y1) / 2 / dims.h) * imgHeight
    container.scrollTo({ top: Math.max(0, bboxCenterPx - container.clientHeight / 2), behavior: 'smooth' })
  }, [corrections, pageDimensions])

  // Déclenche le scroll quand la correction active change
  useEffect(() => {
    const timer = setTimeout(scrollToBbox, 90)
    return () => clearTimeout(timer)
  }, [activeCorrId, scrollToBbox])

  const selectCorrection = useCallback((corr: Correction) => {
    setActiveCorrId(corr.id)
    setFlashKey(k => k + 1)
    loadPageIfNeeded(corr.page - 1)
    setViewPageNum(null) // retour au suivi automatique de la correction
  }, [loadPageIfNeeded])

  // Click sur un overlay PDF → sélectionne la correction ET scroll la liste jusqu'à sa carte
  const selectCorrectionFromOverlay = useCallback((corr: Correction) => {
    selectCorrection(corr)
    setTimeout(() => {
      const container = correctionsContainerRef.current
      if (!container) return
      const card = container.querySelector(`[data-corr-id="${corr.id}"]`) as HTMLElement | null
      if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 60)
  }, [selectCorrection])

  // ── Multi-sélection ────────────────────────────────────────────────────────
  const toggleSelect = useCallback((e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const clearSelection = useCallback(() => setSelectedIds(new Set()), [])

  const handleBulkFP = useCallback(async () => {
    if (selectedIds.size === 0 || bulkFpLoading) return
    setBulkFpLoading(true)
    try {
      const ids = [...selectedIds]
      await Promise.all(
        ids.map(id =>
          apiFetch(`${BACKEND_URL}/api/jobs/${job.id}/corrections/${id}/reject`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason_codes: [], comment: null }),
          }).catch(() => {/* best-effort */})
        )
      )
      setRejectedIds(prev => new Set([...prev, ...ids]))
      setSelectedIds(new Set())
    } finally {
      setBulkFpLoading(false)
    }
  }, [selectedIds, bulkFpLoading, job.id])

  // ── Scroll sync handler — scroll seul, pas de sélection auto ──────────────
  // L'utilisateur doit cliquer pour sélectionner une correction.
  // eslint-disable-next-line @typescript-eslint/no-empty-function
  const handleCorrListScroll = useCallback(() => {}, [])

  // ── Load corrections ───────────────────────────────────────────────────────
  async function loadCorrections() {
    if (corrections.length > 0) { setShowCorrections(true); return }
    setLoadingCorr(true)
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/jobs/${job.id}/corrections`)
      if (!res.ok) throw new Error()
      const data = await res.json()
      const corrList: Correction[] = data.corrections || []
      setCorrections(corrList)
      setPinnedIds(new Set(corrList.filter(c => c.pinned).map(c => c.id)))
      setLikedIds(new Set(corrList.filter(c => c.liked).map(c => c.id)))
      setShowCorrections(true)
      if (corrList.length > 0) {
        setActiveCorrId(corrList[0].id)
        loadPageIfNeeded(corrList[0].page - 1)
      }

      // Restaurer l'état FP depuis la DB (is_false_positive persisté côté serveur)
      const fpIds = corrList.filter(c => c.is_false_positive).map(c => c.id)
      if (fpIds.length > 0) setRejectedIds(new Set(fpIds))

      // Auto-rejeter silencieusement les coupures syllabiques non encore marquées
      const syllabicIds = corrList
        .filter(c => !c.is_false_positive && c.description?.toUpperCase().includes('COUPURE SYLLABIQUE'))
        .map(c => c.id)
      if (syllabicIds.length > 0) {
        setRejectedIds(prev => new Set([...prev, ...syllabicIds]))
        // Persister en DB (idempotent, fire-and-forget)
        apiFetch(`${BACKEND_URL}/api/jobs/${job.id}/corrections/auto-reject-syllabic`, { method: 'POST' })
          .catch(() => { /* best-effort */ })
      }
    } catch {
      alert('Erreur lors du chargement des corrections.')
    } finally {
      setLoadingCorr(false)
    }
  }

  function toggleCorrections() {
    if (showCorrections) { setShowCorrections(false) } else { loadCorrections() }
  }

  // ── Feedback ───────────────────────────────────────────────────────────────
  const openFeedbackModal = useCallback((corr: Correction) => {
    if (rejectedIds.has(corr.id)) return
    setFeedbackTargetCorr(corr)
  }, [rejectedIds])

  const handleRejectConfirm = useCallback(async (reasonCodes: string[], comment: string | null) => {
    if (!feedbackTargetCorr) return
    const corr = feedbackTargetCorr
    setFeedbackTargetCorr(null)
    try {
      await apiFetch(`${BACKEND_URL}/api/jobs/${job.id}/corrections/${corr.id}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason_codes: reasonCodes, comment }),
      })
      setRejectedIds(prev => new Set([...prev, corr.id]))
    } catch { /* best-effort */ }
  }, [feedbackTargetCorr, job.id])

  const handleUnreject = useCallback(async (corrId: string) => {
    try {
      await apiFetch(`${BACKEND_URL}/api/jobs/${job.id}/corrections/${corrId}/reject`, { method: 'DELETE' })
      setRejectedIds(prev => { const next = new Set(prev); next.delete(corrId); return next })
    } catch { /* best-effort */ }
  }, [job.id])

  // ── Pin ────────────────────────────────────────────────────────────────────
  const togglePin = useCallback(async (corrId: string) => {
    // Capture l'état précédent DANS le setter (jamais stale même en clics rapides)
    let wasPinned = false
    setPinnedIds(prev => {
      wasPinned = prev.has(corrId)
      const next = new Set(prev)
      if (wasPinned) next.delete(corrId); else next.add(corrId)
      return next
    })
    // Flash de fond au pin (pas au dépin)
    if (!wasPinned) {
      setPinFlashedId(corrId)
      setTimeout(() => setPinFlashedId(null), 850)
    }
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/jobs/${job.id}/corrections/${corrId}/pin`, { method: 'PATCH' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
    } catch (err) {
      console.error('[pin] échec :', err)
      // Revert : restaurer l'état d'avant l'optimistic update
      setPinnedIds(prev => {
        const next = new Set(prev)
        if (wasPinned) next.add(corrId); else next.delete(corrId)
        return next
      })
    }
  }, [job.id])

  // ── Like / Cœur ────────────────────────────────────────────────────────────
  const toggleLike = useCallback(async (corrId: string) => {
    let wasLiked = false
    setLikedIds(prev => {
      wasLiked = prev.has(corrId)
      const next = new Set(prev)
      if (wasLiked) next.delete(corrId); else next.add(corrId)
      return next
    })
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/jobs/${job.id}/corrections/${corrId}/like`, { method: 'PATCH' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
    } catch (err) {
      console.error('[like] échec :', err)
      setLikedIds(prev => {
        const next = new Set(prev)
        if (wasLiked) next.add(corrId); else next.delete(corrId)
        return next
      })
    }
  }, [job.id])

  // ── Copy ───────────────────────────────────────────────────────────────────
  const handleCopy = useCallback((corr: Correction) => {
    const lines = [
      `p.${corr.page} — ${corr.category} → ${corr.description}`,
      corr.explanation,
      corr.corrected_text
        ? `Correction : « ${corr.original_text} » → « ${corr.corrected_text} »`
        : `Texte : « ${corr.original_text} »`,
    ].filter(Boolean).join('\n')
    navigator.clipboard.writeText(lines).then(() => {
      setCopiedId(corr.id)
      setTimeout(() => setCopiedId(null), 1500)
    })
  }, [])

  // ── Render italic markers (*text* → <em>) ──────────────────────────────────
  function renderText(text: string): Array<string | JSX.Element> {
    const parts = text.split(/(\*[^*]+\*)/)
    return parts.map((part, i) => {
      if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
        return <em key={i}>{part.slice(1, -1)}</em>
      }
      return part
    })
  }

  // ── PDF navigation prev/next ───────────────────────────────────────────────
  const handleNavPrev = useCallback(() => {
    const displayed = displayedCorrectionsRef.current
    const idx = displayed.findIndex(c => c.id === activeCorrId)
    if (idx > 0) {
      const prev = displayed[idx - 1]
      selectCorrection(prev)
      setTimeout(() => {
        const el = correctionsContainerRef.current?.querySelector(`[data-corr-id="${prev.id}"]`) as HTMLElement | null
        el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      }, 50)
    }
  }, [activeCorrId, selectCorrection])

  const handleNavNext = useCallback(() => {
    const displayed = displayedCorrectionsRef.current
    const idx = displayed.findIndex(c => c.id === activeCorrId)
    if (idx !== -1 && idx < displayed.length - 1) {
      const next = displayed[idx + 1]
      selectCorrection(next)
      setTimeout(() => {
        const el = correctionsContainerRef.current?.querySelector(`[data-corr-id="${next.id}"]`) as HTMLElement | null
        el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      }, 50)
    }
  }, [activeCorrId, selectCorrection])

  // ── Raccourcis clavier ←/→ ────────────────────────────────────────────────
  useEffect(() => {
    if (!showCorrections) return
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return
      if (e.key === 'ArrowLeft') { e.preventDefault(); handleNavPrev() }
      if (e.key === 'ArrowRight') { e.preventDefault(); handleNavNext() }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [showCorrections, handleNavPrev, handleNavNext])

  // ── Add correction from PDF click ──────────────────────────────────────────
  const handlePdfImageClick = useCallback(() => {
    const corr = corrections.find(c => c.id === activeCorrId) || null
    if (!corr) return
    setAddCorrPage(corr.page)
    setAddCorrData({ category: 'A', original_text: '', corrected_text: '', description: '' })
    setAddCorrFormOpen(true)
  }, [corrections, activeCorrId])

  const handleAddCorrSubmit = useCallback(async () => {
    if (!addCorrData.original_text.trim()) return
    setAddCorrLoading(true)
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/jobs/${job.id}/corrections/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          page_number: addCorrPage,
          category: addCorrData.category,
          original_text: addCorrData.original_text.trim(),
          corrected_text: addCorrData.corrected_text.trim() || null,
          description: addCorrData.description.trim() || null,
        }),
      })
      if (!res.ok) throw new Error()
      const data = await res.json()
      const newCorr: Correction = {
        id: data.id,
        page: addCorrPage,
        category: addCorrData.category,
        original_text: addCorrData.original_text.trim(),
        corrected_text: addCorrData.corrected_text.trim() || null,
        description: addCorrData.description.trim() || '',
        explanation: '',
        source: '',
        annotation_type: 'Highlight',
        confidence: 'Probable',
        is_user_added: true,
      }
      setCorrections(prev => [...prev, newCorr].sort((a, b) => a.page - b.page))
      setAddCorrFormOpen(false)
      selectCorrection(newCorr)
    } catch {
      alert("Erreur lors de l'ajout de la correction.")
    } finally {
      setAddCorrLoading(false)
    }
  }, [addCorrData, addCorrPage, job.id, selectCorrection])

  // ── Downloads ──────────────────────────────────────────────────────────────
  async function handleDownload() {
    setDownloading(true)
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/jobs/${job.id}/download`)
      if (!res.ok) throw new Error()
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `corrected_${job.filename}`; a.click()
      URL.revokeObjectURL(url)
    } catch { alert('Erreur lors du téléchargement.') }
    finally { setDownloading(false) }
  }

  async function handleDownloadDocx() {
    setDownloadingDocx(true)
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/jobs/${job.id}/download-docx`)
      if (!res.ok) throw new Error()
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const stem = job.filename.replace(/\.pdf$/i, '')
      a.download = `rapport_${stem}.docx`; a.click()
      URL.revokeObjectURL(url)
    } catch { alert('Erreur lors de la génération du rapport Word.') }
    finally { setDownloadingDocx(false) }
  }

  // ── Derived values ─────────────────────────────────────────────────────────
  const totalCorrections = job.corrections_count
  const annotatedInPdf = job.annotated_count ?? totalCorrections
  const hNotAnnotated = job.h_not_annotated_count ?? 0
  const byCategory = job.corrections_by_category || {}
  const catEntries = Object.entries(byCategory).filter(([, v]) => v > 0).sort(([a], [b]) => a.localeCompare(b))
  const presentCats = Array.from(new Set(corrections.map(c => c.category))).sort()
  const showPdfDownload = job.generate_pdf !== false

  const _searchLower = searchQuery.trim().toLowerCase()
  const _isFpTab = filterCat === 'fp'
  const allFiltered = (
    _isFpTab
      ? corrections.filter(c => rejectedIds.has(c.id))
      : (filterCat === 'all' ? corrections : corrections.filter(c => c.category === filterCat))
          .filter(c => !rejectedIds.has(c.id))
          .filter(c => !filterPin || pinnedIds.has(c.id))
          .filter(c => !filterLike || likedIds.has(c.id))
  )
    .filter(c => {
      if (!_searchLower) return true
      return (
        c.original_text.toLowerCase().includes(_searchLower) ||
        (c.corrected_text || '').toLowerCase().includes(_searchLower) ||
        (c.description || '').toLowerCase().includes(_searchLower) ||
        (c.explanation || '').toLowerCase().includes(_searchLower)
      )
    })
    .slice()
    .sort((a, b) => {
      if (sortBy === 'confidence') {
        const diff = (_CONF_RANK[a.confidence] ?? 1) - (_CONF_RANK[b.confidence] ?? 1)
        return diff !== 0 ? diff : a.page - b.page
      }
      return a.page - b.page
    })

  // Corrections avec position PDF connue (onglet FP : toutes visibles)
  const locatedCorrections = _isFpTab ? allFiltered : allFiltered.filter(c => c.bbox != null)
  // Corrections sans position PDF (masquées en onglet FP)
  const unlocatedCorrections = _isFpTab ? [] : allFiltered.filter(c => !c.bbox)

  const displayedLocated = locatedCorrections.slice(0, 150)
  const hiddenLocatedCount = locatedCorrections.length - displayedLocated.length
  const displayedUnlocated = showUnlocated ? unlocatedCorrections.slice(0, 100) : []

  // Liste combinée pour navigation prev/next
  const displayedCorrections = showUnlocated
    ? [...displayedLocated, ...displayedUnlocated]
    : displayedLocated
  const hiddenCount = hiddenLocatedCount

  // Détection des corrections contradictoires (A→B et B→A)
  const conflictIds = useMemo(() => {
    const ids = new Set<string>()
    if (corrections.length < 2) return ids
    for (let i = 0; i < corrections.length; i++) {
      for (let j = i + 1; j < corrections.length; j++) {
        const a = corrections[i], b = corrections[j]
        if (!a.corrected_text || !b.corrected_text) continue
        const aO = a.original_text.trim().toLowerCase()
        const aC = a.corrected_text.trim().toLowerCase()
        const bO = b.original_text.trim().toLowerCase()
        const bC = b.corrected_text.trim().toLowerCase()
        if (aO === bC && bO === aC) { ids.add(a.id); ids.add(b.id) }
      }
    }
    return ids
  }, [corrections])

  // Keep ref in sync with computed value
  displayedCorrectionsRef.current = displayedCorrections

  // ── Pages uniques ayant au moins une correction ────────────────────────────
  const pagesWithCorrections = useMemo(() => {
    const pages = Array.from(new Set(corrections.map(c => c.page - 1))).sort((a, b) => a - b)
    return pages
  }, [corrections])

  // Précharger les vignettes quand le sidebar est visible
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!showThumbnails || pagesWithCorrections.length === 0) return
    let cancelled = false
    let i = 0
    const loadNext = () => {
      if (cancelled || i >= pagesWithCorrections.length) return
      loadPageIfNeeded(pagesWithCorrections[i])
      i++
      setTimeout(loadNext, 120)
    }
    loadNext()
    return () => { cancelled = true }
  }, [showThumbnails, pagesWithCorrections, loadPageIfNeeded])

  const activeCorr = corrections.find(c => c.id === activeCorrId) || null
  const activeMeta = activeCorr ? CATEGORY_META[activeCorr.category] : null
  const activePageNum = activeCorr ? activeCorr.page - 1 : null
  // displayedPageNum : page manually navigated to (takes priority) or active correction page
  const displayedPageNum = viewPageNum ?? activePageNum
  const displayedPageImageUrl = displayedPageNum !== null ? pageImageCache[displayedPageNum] : undefined
  const displayedPageLoading = displayedPageNum !== null && displayedPageImageUrl === ''
  const activeIsRejected = activeCorr ? rejectedIds.has(activeCorr.id) : false
  const activeIsH = activeCorr?.category === 'H'

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="w-full">

      {/* ── Summary view (when split view is closed) ──────────────────────── */}
      {!showCorrections && (
        <div className="max-w-xl mx-auto space-y-5">

          {/* Success header */}
          <div className="rounded-3xl bg-gradient-to-br from-sage-700 via-sage-600 to-sage-400 p-6 text-white shadow-warm-lg">
            <div className="flex items-start gap-4">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-white/20">
                <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h2 className="text-xl font-bold">Correction terminée</h2>
                  {job.created_at && (
                    <span className="text-xs rounded-full bg-white/20 px-2 py-0.5 text-sage-100">
                      {new Date(job.created_at).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' })}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sage-200 text-sm truncate max-w-xs">{job.filename}</p>
                <p className="mt-2 text-white font-bold text-3xl">
                  {totalCorrections.toLocaleString('fr-FR')}
                  <span className="text-xl font-semibold ml-2">correction{totalCorrections > 1 ? 's' : ''}</span>
                </p>
                {showPdfDownload && annotatedInPdf < totalCorrections && (
                  <p className="mt-1 text-sage-200 text-xs">
                    {annotatedInPdf} dans le PDF
                    {hNotAnnotated > 0 && ` · ${hNotAnnotated} uniquement dans le rapport`}
                  </p>
                )}
                {job.actual_cost_usd != null && (
                  <p className="mt-1.5 text-sage-200 text-xs font-mono">
                    Coût API réel : ${job.actual_cost_usd.toFixed(3)}
                    {job.estimated_cost_usd != null && (
                      <span className="ml-1.5 opacity-60">(estimé ${job.estimated_cost_usd.toFixed(3)})</span>
                    )}
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Category breakdown */}
          {catEntries.length > 0 && (
            <div className="rounded-2xl bg-white border border-stone-200 shadow-warm-sm overflow-hidden">
              <div className="px-5 py-4 border-b border-stone-100">
                <p className="text-sm font-semibold text-stone-800">Répartition par catégorie</p>
              </div>
              <div className="divide-y divide-stone-100">
                {catEntries.map(([cat, count]) => {
                  const meta = CATEGORY_META[cat]
                  if (!meta) return null
                  const pct = totalCorrections > 0 ? Math.round((count / totalCorrections) * 100) : 0
                  return (
                    <div key={cat} className="flex items-center gap-3 px-5 py-3">
                      <span className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${meta.bg} ${meta.color}`}>
                        {cat}
                      </span>
                      <span className="flex-1 text-sm text-stone-700">{meta.label}</span>
                      <div className="flex items-center gap-2">
                        <div className="hidden sm:block h-1.5 w-20 rounded-full bg-stone-100">
                          <div className={`h-full rounded-full ${meta.dot}`} style={{ width: `${pct}%` }} />
                        </div>
                        <span className={`text-sm font-semibold ${meta.color}`}>{count}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* View corrections button — CTA principale */}
          <button
            onClick={toggleCorrections}
            disabled={loadingCorr}
            className="btn-primary w-full py-4 text-base rounded-2xl disabled:opacity-60"
          >
            {loadingCorr ? (
              <>
                <svg className="h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Chargement…
              </>
            ) : (
              <>
                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h16" />
                </svg>
                Consulter les corrections
              </>
            )}
          </button>

          {/* Download PDF (only if generated) */}
          {showPdfDownload && (
            <button
              onClick={handleDownload}
              disabled={downloading}
              className="btn-secondary w-full py-3.5 rounded-2xl disabled:opacity-60"
            >
              {downloading ? (
                <>
                  <svg className="h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Téléchargement…
                </>
              ) : (
                <>
                  <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                  </svg>
                  Télécharger le PDF annoté
                </>
              )}
            </button>
          )}

          {/* Download DOCX */}
          <button
            onClick={handleDownloadDocx}
            disabled={downloadingDocx}
            className="btn-secondary w-full py-3.5 rounded-2xl disabled:opacity-60"
          >
            {downloadingDocx ? (
              <>
                <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Génération du rapport…
              </>
            ) : (
              <>
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                </svg>
                Exporter le rapport Word (.docx)
              </>
            )}
          </button>

          <button
            onClick={onNewDocument}
            className="w-full rounded-2xl border border-stone-200 bg-stone-50 px-6 py-3 text-sm font-medium text-stone-500 hover:bg-stone-100 hover:text-stone-700 transition-all duration-150"
          >
            Corriger un nouveau document
          </button>

          {showPdfDownload && (
            <p className="text-center text-xs text-stone-400 leading-relaxed">
              Le PDF contient des annotations natives visibles dans Adobe Acrobat, macOS Aperçu et tout lecteur compatible. Une page de synthèse est ajoutée en fin de document.
            </p>
          )}
        </div>
      )}

      {/* ── SPLIT VIEW OVERLAY (Acrobat-style) ────────────────────────────── */}
      {showCorrections && corrections.length > 0 && (
        <div
          className="fixed inset-x-0 bottom-0 z-20 flex flex-col bg-white"
          style={{ top: '65px' }}
        >
          {/* ── Top bar ──────────────────────────────────────────────────── */}
          <div className="flex items-center gap-2 px-3 py-2 border-b border-stone-200 bg-white shrink-0">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-slate-800 truncate">{job.filename}</p>
              <p className="text-[11px] text-slate-400">
                {totalCorrections} correction{totalCorrections > 1 ? 's' : ''}
                {activeCorr && <> · p.{activeCorr.page}</>}
                {job.actual_cost_usd != null && (
                  <span className="ml-1.5 font-mono text-slate-500">${job.actual_cost_usd.toFixed(3)}</span>
                )}
              </p>
            </div>

            {/* DOCX download */}
            <button
              onClick={handleDownloadDocx}
              disabled={downloadingDocx}
              className="flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50 transition-colors shrink-0"
              title="Rapport Word"
            >
              {downloadingDocx
                ? <svg className="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                : <><svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" /></svg>.docx</>
              }
            </button>

            {/* PDF download (only if annotated PDF was generated) */}
            {showPdfDownload && (
              <button
                onClick={handleDownload}
                disabled={downloading}
                className="flex items-center gap-1 rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1.5 text-xs font-medium text-indigo-600 hover:bg-indigo-100 disabled:opacity-50 transition-colors shrink-0"
                title="PDF annoté"
              >
                {downloading
                  ? <svg className="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>
                  : <><svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" /></svg>.pdf</>
                }
              </button>
            )}

            {/* New document */}
            <button
              onClick={() => { setShowCorrections(false); onNewDocument() }}
              className="hidden sm:flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-500 hover:bg-slate-50 transition-colors shrink-0"
              title="Nouveau document"
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              Nouveau
            </button>

            {/* Close split view */}
            <button
              onClick={() => setShowCorrections(false)}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors"
              title="Fermer"
            >
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* ── Content ──────────────────────────────────────────────────── */}
          <div className="flex-1 flex flex-col lg:flex-row overflow-hidden">

            {/* ── THUMBNAIL SIDEBAR ─────────────────────────────────────── */}
            {showThumbnails && corrections.length > 0 && (
              <div className="hidden lg:flex flex-col w-[88px] shrink-0 bg-slate-900 border-r border-slate-800 overflow-y-auto">
                {pagesWithCorrections.map(pageIdx => {
                  const corrOnPage = corrections.filter(c => c.page - 1 === pageIdx)
                  const isActivePage = activeCorr ? activeCorr.page - 1 === pageIdx : false
                  const imgUrl = pageImageCache[pageIdx]
                  const cats = Array.from(new Set(corrOnPage.map(c => c.category))).sort()
                  return (
                    <button
                      key={pageIdx}
                      onClick={() => {
                        const first = displayedCorrections.find(c => c.page - 1 === pageIdx)
                          || corrections.find(c => c.page - 1 === pageIdx)
                        if (first) selectCorrection(first)
                      }}
                      className={[
                        'flex flex-col items-center gap-1 py-2 px-1.5 border-b border-slate-800 transition-colors shrink-0',
                        isActivePage ? 'bg-slate-700' : 'hover:bg-slate-800',
                      ].join(' ')}
                      title={`Page ${pageIdx + 1} · ${corrOnPage.length} correction${corrOnPage.length > 1 ? 's' : ''}`}
                    >
                      <div className="w-full aspect-[0.707] bg-slate-700 rounded overflow-hidden relative shadow">
                        {imgUrl
                          ? <img src={imgUrl} alt={`p.${pageIdx + 1}`} className="w-full h-full object-cover object-top" />
                          : <div className="w-full h-full bg-slate-700 animate-pulse" />
                        }
                        {isActivePage && (
                          <div className="absolute inset-0 ring-2 ring-indigo-400 rounded pointer-events-none" />
                        )}
                      </div>
                      <div className="flex items-center gap-0.5 w-full justify-between px-0.5">
                        <span className="text-[9px] text-slate-400 leading-none">{pageIdx + 1}</span>
                        <div className="flex gap-px flex-wrap justify-end">
                          {cats.map(cat => {
                            const meta = CATEGORY_META[cat]
                            return <span key={cat} className={`inline-block w-1.5 h-1.5 rounded-full ${meta?.dot || 'bg-slate-500'}`} />
                          })}
                        </div>
                      </div>
                    </button>
                  )
                })}
              </div>
            )}

            {/* ── LEFT: PDF viewer (62%) ────────────────────────────────── */}
            <div className="h-[42vh] lg:h-auto lg:flex-[62] bg-slate-800 flex flex-col overflow-hidden">
              {!activeCorr ? (
                <div className="flex flex-col items-center justify-center flex-1 gap-3 px-6">
                  <svg className="h-12 w-12 text-slate-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  <p className="text-sm text-slate-500 text-center">Sélectionnez une correction</p>
                </div>
              ) : (
                <>
                  {/* Dark info bar */}
                  <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-900/80 border-b border-slate-700 shrink-0">
                    {activeMeta && (
                      <span className={`inline-flex shrink-0 items-center justify-center rounded-full text-[10px] font-bold px-2 py-0.5 ${activeMeta.bg} ${activeMeta.color}`}>
                        {activeMeta.label}
                      </span>
                    )}
                    {activeCorr.is_user_added && (
                      <span className="inline-flex shrink-0 items-center justify-center rounded-full text-[10px] font-bold px-2 py-0.5 bg-indigo-100 text-indigo-700">Vous</span>
                    )}
                    <p className="text-xs font-medium text-slate-300 truncate flex-1">
                      {activeCorr.description}
                      <span className="text-slate-500 ml-1">· p.{activeCorr.page}</span>
                    </p>
                    {/* Navigation prev/next */}
                    {(() => {
                      const displayed = displayedCorrectionsRef.current
                      const navIdx = displayed.findIndex(c => c.id === activeCorrId)
                      return (
                        <div className="flex items-center gap-0.5 shrink-0">
                          <button
                            onClick={handleNavPrev}
                            disabled={navIdx <= 0}
                            className="h-5 w-5 flex items-center justify-center rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700 disabled:opacity-25 disabled:cursor-default text-base leading-none"
                            title="Correction précédente"
                          >‹</button>
                          <span className="text-[10px] text-slate-500 w-12 text-center tabular-nums">
                            {navIdx !== -1 ? `${navIdx + 1}/${displayed.length}` : ''}
                          </span>
                          <button
                            onClick={handleNavNext}
                            disabled={navIdx === -1 || navIdx >= displayedCorrectionsRef.current.length - 1}
                            className="h-5 w-5 flex items-center justify-center rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700 disabled:opacity-25 disabled:cursor-default text-base leading-none"
                            title="Correction suivante"
                          >›</button>
                        </div>
                      )
                    })()}
                    {/* Page navigation — indépendant de la sélection de correction */}
                    {job.pages_count && job.pages_count > 1 && (
                      <div className="flex items-center gap-0.5 shrink-0">
                        <button
                          onClick={() => {
                            const cur = displayedPageNum ?? 0
                            if (cur > 0) { const p = cur - 1; setViewPageNum(p); loadPageIfNeeded(p) }
                          }}
                          disabled={(displayedPageNum ?? 0) <= 0}
                          className="h-5 w-5 flex items-center justify-center rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700 disabled:opacity-25 disabled:cursor-default text-base leading-none"
                          title="Page précédente"
                        >‹</button>
                        <span className="text-[10px] text-slate-400 w-14 text-center tabular-nums select-none">
                          {(displayedPageNum ?? 0) + 1}/{job.pages_count}
                        </span>
                        <button
                          onClick={() => {
                            const cur = displayedPageNum ?? 0
                            if (cur < job.pages_count! - 1) { const p = cur + 1; setViewPageNum(p); loadPageIfNeeded(p) }
                          }}
                          disabled={(displayedPageNum ?? 0) >= job.pages_count - 1}
                          className="h-5 w-5 flex items-center justify-center rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700 disabled:opacity-25 disabled:cursor-default text-base leading-none"
                          title="Page suivante"
                        >›</button>
                      </div>
                    )}
                    {/* Zoom controls */}
                    <div className="flex items-center gap-0.5 shrink-0">
                      <button
                        onClick={() => setZoomScale(s => Math.max(0.25, parseFloat((s - 0.10).toFixed(2))))}
                        className="h-5 w-5 flex items-center justify-center rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700 text-sm leading-none"
                        title="Dézoomer"
                      >−</button>
                      <span className="text-[10px] text-slate-500 w-9 text-center tabular-nums">{Math.round(zoomScale * 100)}%</span>
                      <button
                        onClick={() => setZoomScale(s => Math.min(3, parseFloat((s + 0.10).toFixed(2))))}
                        className="h-5 w-5 flex items-center justify-center rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700 text-sm leading-none"
                        title="Zoomer"
                      >+</button>
                    </div>
                    {activeCorr && !activeCorr.bbox && (
                      <span className="text-[10px] text-slate-500 italic shrink-0" title="Le texte exact n'a pas pu être localisé dans le PDF">position inconnue</span>
                    )}
                    {showPdfDownload && activeCorr?.bbox && (
                      <span className="text-[10px] text-slate-600 italic shrink-0">annoté</span>
                    )}
                    {/* Toggle vignettes */}
                    <button
                      onClick={() => setShowThumbnails(s => !s)}
                      className={[
                        'hidden lg:flex h-5 w-5 items-center justify-center rounded text-slate-400 hover:text-slate-200 hover:bg-slate-700 shrink-0',
                        showThumbnails ? 'text-indigo-400' : '',
                      ].join(' ')}
                      title={showThumbnails ? 'Masquer les vignettes' : 'Afficher les vignettes'}
                    >
                      <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                      </svg>
                    </button>
                  </div>

                  {/* Page image — scrollable with zoom + bbox overlay */}
                  <div ref={pdfScrollRef} className="flex-1 overflow-auto flex flex-col items-center py-8 px-6 bg-slate-700">
                    {displayedPageLoading && (
                      <div className="flex flex-col items-center gap-3 py-16">
                        <svg className="h-7 w-7 animate-spin text-indigo-400" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                        </svg>
                        <span className="text-xs text-slate-500">Chargement…</span>
                      </div>
                    )}
                    {displayedPageImageUrl && (() => {
                      const dims = displayedPageNum !== null ? pageDimensions[displayedPageNum] : undefined
                      const pageCorrsWithBbox = dims
                        ? corrections.filter(c =>
                            c.page - 1 === displayedPageNum &&
                            c.bbox != null &&
                            !rejectedIds.has(c.id)
                          )
                        : []
                      // La correction active est mise en évidence seulement si elle est sur la page affichée
                      const showActiveHighlight = activeCorr?.bbox && activeCorr.page - 1 === displayedPageNum
                      return (
                        <div
                          className="relative shadow-[0_8px_40px_rgba(0,0,0,0.6)] rounded"
                          style={{
                            width: `${zoomScale * 100}%`,
                            maxWidth: zoomScale > 1 ? 'none' : '672px',
                          }}
                        >
                          <img
                            src={displayedPageImageUrl}
                            alt={`Page ${(displayedPageNum ?? 0) + 1}`}
                            onClick={handlePdfImageClick}
                            onLoad={e => {
                              // Fallback dimensions si les headers CORS ne sont pas encore disponibles
                              const img = e.currentTarget
                              const pNum = displayedPageNum
                              if (pNum !== null && !pageDimensions[pNum] && img.naturalWidth > 0) {
                                const w = img.naturalWidth * 72 / 130
                                const h = img.naturalHeight * 72 / 130
                                setPageDimensions(p => ({ ...p, [pNum]: { w, h } }))
                              }
                              scrollToBbox()
                            }}
                            className="w-full block rounded cursor-crosshair"
                            title="Cliquez pour signaler une erreur sur cette page"
                          />
                          {dims && (
                            <>
                              {/* Surligneurs persistants — toutes les corrections de la page */}
                              {pageCorrsWithBbox
                                .filter(c => c.id !== activeCorrId)
                                .map(c => {
                                  const b = c.bbox!
                                  return (
                                    <div
                                      key={`bg-${c.id}`}
                                      onClick={e => { e.stopPropagation(); selectCorrectionFromOverlay(c) }}
                                      title={`${c.category} — ${c.original_text.slice(0, 60)}`}
                                      style={{
                                        position: 'absolute',
                                        left: `${(b.x0 / dims.w) * 100}%`,
                                        top: `${(b.y0 / dims.h) * 100}%`,
                                        width: `${((b.x1 - b.x0) / dims.w) * 100}%`,
                                        height: `${((b.y1 - b.y0) / dims.h) * 100}%`,
                                        minHeight: '8px',
                                        backgroundColor: CATEGORY_BBOX_COLOR[c.category] ?? 'rgba(99,102,241,0.22)',
                                        cursor: 'pointer',
                                        borderRadius: '2px',
                                        zIndex: 3,
                                        transition: 'background-color 0.1s',
                                      }}
                                      onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.backgroundColor = CATEGORY_BBOX_COLOR_BRIGHT[c.category] ?? 'rgba(99,102,241,0.4)' }}
                                      onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.backgroundColor = CATEGORY_BBOX_COLOR[c.category] ?? 'rgba(99,102,241,0.22)' }}
                                    />
                                  )
                                })
                              }
                              {/* Correction active : surligneur plus vif + bordure + flash au clic */}
                              {showActiveHighlight && activeCorr!.bbox && (
                                <>
                                  <div
                                    style={{
                                      position: 'absolute',
                                      left: `${(activeCorr!.bbox.x0 / dims.w) * 100}%`,
                                      top: `${(activeCorr!.bbox.y0 / dims.h) * 100}%`,
                                      width: `${((activeCorr!.bbox.x1 - activeCorr!.bbox.x0) / dims.w) * 100}%`,
                                      height: `${((activeCorr!.bbox.y1 - activeCorr!.bbox.y0) / dims.h) * 100}%`,
                                      backgroundColor: CATEGORY_BBOX_COLOR_BRIGHT[activeCorr!.category] ?? 'rgba(99,102,241,0.48)',
                                      border: `2px solid ${CATEGORY_BORDER_COLOR[activeCorr!.category] ?? '#6366f1'}`,
                                      pointerEvents: 'none',
                                      borderRadius: '2px',
                                      boxSizing: 'border-box',
                                    }}
                                  />
                                  <div
                                    key={`flash-${activeCorrId}-${flashKey}`}
                                    className="bbox-flash"
                                    style={{
                                      position: 'absolute',
                                      left: `${(activeCorr!.bbox.x0 / dims.w) * 100}%`,
                                      top: `${(activeCorr!.bbox.y0 / dims.h) * 100}%`,
                                      width: `${((activeCorr!.bbox.x1 - activeCorr!.bbox.x0) / dims.w) * 100}%`,
                                      height: `${((activeCorr!.bbox.y1 - activeCorr!.bbox.y0) / dims.h) * 100}%`,
                                      pointerEvents: 'none',
                                    }}
                                  />
                                </>
                              )}
                            </>
                          )}
                        </div>
                      )
                    })()}
                    {!displayedPageLoading && !displayedPageImageUrl && displayedPageNum !== null && pageImageCache[displayedPageNum] === undefined && (
                      <div className="flex flex-col items-center gap-2 py-16 text-sm text-slate-500">
                        <p>Erreur de chargement</p>
                        <button
                          onClick={() => activeCorr && selectCorrection(activeCorr)}
                          className="text-indigo-400 hover:underline text-xs"
                        >
                          Réessayer
                        </button>
                      </div>
                    )}
                  </div>

                  {/* Source URL pour correction H active */}
                  {activeIsH && activeCorr?.source && (
                    <div className="px-3 py-1.5 bg-amber-900/20 border-t border-amber-800/30 shrink-0 flex items-center gap-1.5">
                      <span className="text-amber-400 text-[11px] shrink-0">🔗</span>
                      {activeCorr.source.startsWith('http') ? (
                        <a
                          href={activeCorr.source}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-amber-400 hover:text-amber-300 text-[11px] underline truncate"
                        >
                          {activeCorr.source.length > 80 ? activeCorr.source.slice(0, 80) + '…' : activeCorr.source}
                        </a>
                      ) : (
                        <span className="text-amber-400 text-[11px] truncate">Vérifié via {activeCorr.source}</span>
                      )}
                    </div>
                  )}

                  {/* Actions panneau détail */}
                  <div className="px-4 py-2.5 border-t border-slate-700 bg-slate-900/50 shrink-0">
                    {activeIsRejected ? (
                      <p className="text-center text-xs text-slate-500 italic">✓ Signalé comme faux positif</p>
                    ) : (
                      <div className="flex gap-2">
                        <button
                          onClick={() => toggleLike(activeCorr.id)}
                          className={[
                            'flex-1 rounded-xl border px-3 py-2 text-xs font-medium transition-colors flex items-center justify-center gap-1.5',
                            likedIds.has(activeCorr.id)
                              ? 'border-rose-500/60 bg-rose-900/30 text-rose-400 hover:bg-rose-900/50'
                              : 'border-slate-600/40 bg-slate-800/40 text-slate-400 hover:bg-slate-700/40',
                          ].join(' ')}
                        >
                          ❤️ {likedIds.has(activeCorr.id) ? 'Pertinent ✓' : 'Pertinent'}
                        </button>
                        <button
                          onClick={() => openFeedbackModal(activeCorr)}
                          className={[
                            'flex-1 rounded-xl border px-3 py-2 text-xs font-medium transition-colors flex items-center justify-center gap-1.5',
                            activeIsH
                              ? 'border-amber-600/40 bg-amber-900/20 text-amber-400 hover:bg-amber-900/40'
                              : 'border-red-600/40 bg-red-900/20 text-red-400 hover:bg-red-900/40',
                          ].join(' ')}
                        >
                          👎 Faux positif
                        </button>
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>

            {/* ── RIGHT: Corrections list (38%) ────────────────────────── */}
            <div className="flex-1 lg:flex-[38] flex flex-col overflow-hidden border-t lg:border-t-0 lg:border-l border-slate-200">

              {/* Filter + sort bar */}
              <div className="px-3 py-2 border-b border-stone-100 flex flex-wrap gap-1.5 items-center bg-stone-50 shrink-0">
                {job.pages_count && (
                  <span className="text-[10px] text-stone-400 font-medium shrink-0 mr-1">
                    {job.pages_count} p. analysées
                  </span>
                )}
                <button
                  onClick={() => setFilterCat('all')}
                  className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-all duration-150 ${
                    filterCat === 'all'
                      ? 'bg-sage-600 text-white shadow-warm-sm'
                      : 'bg-white text-stone-600 hover:bg-stone-100 border border-stone-200 hover:border-stone-300'
                  }`}
                >
                  Tout ({corrections.length})
                </button>
                {presentCats.map(cat => {
                  const meta = CATEGORY_META[cat]
                  const count = corrections.filter(c => c.category === cat).length
                  return (
                    <button
                      key={cat}
                      onClick={() => setFilterCat(cat)}
                      className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-all duration-150 whitespace-nowrap ${
                        filterCat === cat
                          ? `${meta?.bg || 'bg-stone-100'} ${meta?.color || 'text-stone-700'} border ${meta?.border || 'border-stone-200'} shadow-warm-sm`
                          : 'bg-white text-stone-600 hover:bg-stone-100 border border-stone-200 hover:border-stone-300'
                      }`}
                    >
                      {meta?.label || cat} ({count})
                    </button>
                  )
                })}
                {rejectedIds.size > 0 && (
                  <button
                    onClick={() => { setFilterCat(f => f === 'fp' ? 'all' : 'fp'); setFilterPin(false); setFilterLike(false) }}
                    className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-all duration-150 whitespace-nowrap ${
                      _isFpTab
                        ? 'bg-red-100 text-red-700 border border-red-200 shadow-warm-sm'
                        : 'bg-white text-red-500 hover:bg-red-50 border border-red-200 hover:border-red-300'
                    }`}
                  >
                    👎 Faux positifs ({rejectedIds.size})
                  </button>
                )}
                {!_isFpTab && (
                  <>
                    <button
                      onClick={() => setFilterPin(f => !f)}
                      className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-all duration-150 ${
                        filterPin
                          ? 'bg-amber-100 text-amber-700 border border-amber-200 shadow-warm-sm'
                          : 'bg-white text-stone-500 hover:bg-stone-100 border border-stone-200 hover:border-stone-300'
                      }`}
                      title="Filtrer les corrections épinglées"
                    >
                      📌
                    </button>
                    <button
                      onClick={() => setFilterLike(f => !f)}
                      className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-all duration-150 ${
                        filterLike
                          ? 'bg-rose-100 text-rose-700 border border-rose-200 shadow-warm-sm'
                          : 'bg-white text-stone-500 hover:bg-stone-100 border border-stone-200 hover:border-stone-300'
                      }`}
                      title="Filtrer les corrections pertinentes"
                    >
                      ❤️
                    </button>
                  </>
                )}
                <button
                  onClick={() => setSortBy(s => s === 'page' ? 'confidence' : 'page')}
                  className="ml-auto rounded-full px-2.5 py-1 text-[11px] font-medium border border-stone-200 bg-white text-stone-500 hover:bg-stone-100 hover:border-stone-300 transition-all duration-150 shrink-0"
                >
                  {sortBy === 'page' ? 'Par page' : 'Par confiance'}
                </button>
              </div>
              {/* Search bar */}
              <div className="px-3 py-1.5 border-b border-slate-100 bg-white shrink-0">
                <div className="relative flex items-center">
                  <svg className="absolute left-2 h-3.5 w-3.5 text-slate-400 pointer-events-none" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={e => setSearchQuery(e.target.value)}
                    placeholder="Rechercher dans les corrections…"
                    className="w-full rounded-lg border border-slate-200 bg-slate-50 pl-7 pr-7 py-1 text-[12px] text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-1 focus:ring-indigo-300 focus:border-indigo-300 transition-colors"
                  />
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery('')}
                      className="absolute right-2 text-slate-400 hover:text-slate-600 transition-colors"
                      title="Effacer"
                    >
                      <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  )}
                </div>
                {searchQuery && (
                  <p className="mt-1 text-[10px] text-slate-400">
                    {allFiltered.length} résultat{allFiltered.length > 1 ? 's' : ''} pour « {searchQuery} »
                    {allFiltered.length > 0 && (
                      <button
                        className="ml-2 text-indigo-500 hover:underline"
                        onClick={() => {
                          const first = allFiltered[0]
                          if (first) selectCorrection(first)
                        }}
                      >Aller au premier</button>
                    )}
                  </p>
                )}
              </div>

              {/* Scrollable corrections list with scroll sync */}
              <div
                ref={correctionsContainerRef}
                onScroll={handleCorrListScroll}
                className="flex-1 overflow-y-auto divide-y divide-slate-100"
              >
                {displayedLocated.map(corr => {
                  const meta = CATEGORY_META[corr.category]
                  const isH = corr.category === 'H'
                  const isRejected = rejectedIds.has(corr.id)
                  const isActive = activeCorrId === corr.id

                  const isPinned = pinnedIds.has(corr.id)
                  const isLiked = likedIds.has(corr.id)

                  return (
                    <div
                      key={corr.id}
                      data-corr-id={corr.id}
                      onClick={() => (!isRejected || _isFpTab) && selectCorrection(corr)}
                      className={[
                        'px-3 py-2.5 cursor-pointer transition-all group border-l-2',
                        pinFlashedId === corr.id ? 'pin-flash' : '',
                        isActive
                          ? 'bg-indigo-50 border-l-indigo-400'
                          : isRejected && !_isFpTab
                          ? 'bg-slate-50 opacity-40 border-l-transparent cursor-default'
                          : isRejected
                          ? 'bg-red-50/40 border-l-red-200 hover:bg-red-50'
                          : isLiked
                          ? 'bg-rose-50 border-l-rose-300 hover:bg-rose-100'
                          : isPinned
                          ? 'bg-amber-50 border-l-amber-300 hover:bg-amber-100'
                          : isH
                          ? 'bg-amber-50/40 border-l-transparent hover:bg-amber-50 hover:border-l-amber-300'
                          : 'bg-white border-l-transparent hover:bg-slate-50 hover:border-l-slate-200',
                      ].join(' ')}
                    >
                      {/* Top row: checkbox + page + badges + actions */}
                      <div className="flex items-center gap-1.5 flex-wrap">
                        {/* Checkbox multi-sélection — masqué en onglet FP */}
                        {!isRejected && (
                          <div
                            className={[
                              'shrink-0 transition-opacity duration-100',
                              selectedIds.size > 0 ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
                            ].join(' ')}
                            onClick={e => toggleSelect(e, corr.id)}
                          >
                            <div className={[
                              'h-4 w-4 rounded border-2 flex items-center justify-center transition-colors',
                              selectedIds.has(corr.id)
                                ? 'bg-sage-600 border-sage-600'
                                : 'border-stone-300 bg-white hover:border-sage-400',
                            ].join(' ')}>
                              {selectedIds.has(corr.id) && (
                                <svg className="h-2.5 w-2.5 text-white" viewBox="0 0 10 10" fill="currentColor">
                                  <path d="M8.5 2.5L4 7.5l-2-2" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none"/>
                                </svg>
                              )}
                            </div>
                          </div>
                        )}
                        <span className="text-[10px] font-mono font-semibold text-slate-400 shrink-0">
                          p.{corr.page}
                        </span>
                        {meta && (
                          <span className={`text-[10px] font-medium rounded-full px-1.5 py-px ${meta.bg} ${meta.color}`}>
                            {meta.label}
                          </span>
                        )}
                        {corr.is_user_added && (
                          <span className="text-[10px] font-bold rounded-full px-1.5 py-px bg-indigo-100 text-indigo-700">Vous</span>
                        )}
                        {!isRejected && (isH ? (
                          <span className="text-[10px] font-medium rounded-full px-1.5 py-px bg-amber-100 text-amber-700">⚠️ À vérifier</span>
                        ) : corr.confidence && (
                          <span className={`text-[10px] font-medium rounded-full px-1.5 py-px ${CONFIDENCE_COLOR[corr.confidence] || 'bg-slate-100 text-slate-600'}`}>
                            {corr.confidence}
                          </span>
                        ))}
                        {conflictIds.has(corr.id) && !isRejected && (
                          <span
                            className="text-[10px] font-medium rounded-full px-1.5 py-px bg-orange-100 text-orange-700"
                            title="Correction contradictoire avec une autre correction du document"
                          >
                            ⚡ Conflit
                          </span>
                        )}
                        {/* Pin — toujours visible si épinglé, sinon au survol */}
                        {!isRejected && (
                          <button
                            onClick={e => { e.stopPropagation(); togglePin(corr.id) }}
                            className={[
                              'rounded px-1.5 py-1 text-xs border border-transparent transition-all shrink-0',
                              isPinned
                                ? 'text-amber-500 hover:text-amber-700'
                                : 'opacity-0 group-hover:opacity-100 text-slate-300 hover:text-amber-400 hover:border-amber-200',
                            ].join(' ')}
                            title={isPinned ? 'Désépingler' : 'Épingler'}
                          >
                            📌
                          </button>
                        )}
                        {/* Like / Cœur — toujours visible si aimé, sinon au survol */}
                        {!isRejected && (
                          <button
                            onClick={e => { e.stopPropagation(); toggleLike(corr.id) }}
                            className={[
                              'rounded px-1.5 py-1 text-xs border border-transparent transition-all shrink-0',
                              isLiked
                                ? 'text-rose-500 hover:text-rose-700'
                                : 'opacity-0 group-hover:opacity-100 text-slate-300 hover:text-rose-400 hover:border-rose-200',
                            ].join(' ')}
                            title={isLiked ? 'Retirer le cœur' : 'Correction pertinente'}
                          >
                            ❤️
                          </button>
                        )}
                        {/* Onglet FP : bouton Rétablir */}
                        {isRejected && _isFpTab && (
                          <button
                            onClick={e => { e.stopPropagation(); handleUnreject(corr.id) }}
                            className="ml-auto rounded-lg px-2.5 py-1 text-[11px] font-medium border border-green-200 bg-green-50 text-green-700 hover:bg-green-100 transition-colors shrink-0"
                            title="Rétablir cette correction (supprimer le signalement faux positif)"
                          >
                            ↩ Rétablir
                          </button>
                        )}
                        {!isRejected && (
                          <div className="ml-auto flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                            {/* Google */}
                            <button
                              onClick={e => {
                                e.stopPropagation()
                                window.open(`https://www.google.com/search?q=${encodeURIComponent(corr.original_text)}`, '_blank', 'noopener,noreferrer')
                              }}
                              className="rounded p-1 border border-transparent hover:border-blue-200 hover:bg-blue-50 transition-colors"
                              title="Vérifier sur Google"
                            >
                              <svg viewBox="0 0 24 24" width="14" height="14" xmlns="http://www.w3.org/2000/svg">
                                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                              </svg>
                            </button>
                            {/* Larousse */}
                            <button
                              onClick={e => {
                                e.stopPropagation()
                                window.open(`https://www.larousse.fr/dictionnaires/francais/${encodeURIComponent(corr.original_text)}`, '_blank', 'noopener,noreferrer')
                              }}
                              className="rounded p-1 border border-transparent hover:border-orange-200 hover:bg-orange-50 transition-colors"
                              title="Vérifier sur Larousse"
                            >
                              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#f97316" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" xmlns="http://www.w3.org/2000/svg">
                                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/>
                                <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
                              </svg>
                            </button>
                            {/* Wikipedia */}
                            <button
                              onClick={e => {
                                e.stopPropagation()
                                window.open(`https://fr.wikipedia.org/w/index.php?search=${encodeURIComponent(corr.original_text)}`, '_blank', 'noopener,noreferrer')
                              }}
                              className="rounded p-1 border border-transparent hover:border-slate-200 hover:bg-slate-50 transition-colors"
                              title="Vérifier sur Wikipédia"
                            >
                              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="#64748b" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" xmlns="http://www.w3.org/2000/svg">
                                <circle cx="12" cy="12" r="10"/>
                                <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
                              </svg>
                            </button>
                            {/* CNRTL */}
                            <button
                              onClick={e => {
                                e.stopPropagation()
                                const word = corr.original_text.trim().split(/\s+/)[0].replace(/[^a-zA-ZÀ-ÿ'-]/g, '')
                                window.open(`https://www.cnrtl.fr/definition/${encodeURIComponent(word)}`, '_blank', 'noopener,noreferrer')
                              }}
                              className="rounded p-1 border border-transparent hover:border-teal-200 hover:bg-teal-50 transition-colors text-[10px] font-bold text-teal-600"
                              title="Vérifier sur CNRTL (dictionnaire académique)"
                            >CN</button>
                            {/* Le Robert */}
                            <button
                              onClick={e => {
                                e.stopPropagation()
                                const word = corr.original_text.trim().split(/\s+/)[0].replace(/[^a-zA-ZÀ-ÿ'-]/g, '')
                                window.open(`https://dictionnaire.lerobert.com/define/${encodeURIComponent(word)}`, '_blank', 'noopener,noreferrer')
                              }}
                              className="rounded p-1 border border-transparent hover:border-violet-200 hover:bg-violet-50 transition-colors text-[10px] font-bold text-violet-600"
                              title="Vérifier sur Le Robert"
                            >Ro</button>
                            <button
                              onClick={e => { e.stopPropagation(); handleCopy(corr) }}
                              className="rounded px-1.5 py-1 text-xs text-slate-400 hover:text-slate-600 border border-transparent hover:border-slate-200 hover:bg-slate-50"
                              title="Copier"
                            >
                              {copiedId === corr.id ? '✓' : '⧉'}
                            </button>
                            <button
                              onClick={e => { e.stopPropagation(); openFeedbackModal(corr) }}
                              className="rounded px-1.5 py-1 text-xs text-red-400 hover:text-red-600 border border-transparent hover:border-red-200 hover:bg-red-50"
                              title="Faux positif"
                            >
                              👎
                            </button>
                          </div>
                        )}
                      </div>

                      {/* Body */}
                      {(!isRejected || _isFpTab) && (
                        <div className="mt-1 space-y-0.5">
                          {corr.description && (
                            <p className={`text-[11px] font-semibold uppercase tracking-wide truncate ${isH ? 'text-amber-700' : 'text-slate-500'}`}>
                              {corr.description}
                            </p>
                          )}
                          <p className="text-[14px] leading-snug">
                            <span className={isH
                              ? 'text-amber-800 bg-amber-100 rounded px-0.5'
                              : 'line-through text-red-400'
                            }>
                              {renderText(corr.original_text.slice(0, 70))}{corr.original_text.length > 70 ? '…' : ''}
                            </span>
                            {corr.corrected_text && !isH && corr.corrected_text !== corr.original_text && (
                              <span className="text-green-700 font-medium">
                                {' → '}{renderText(corr.corrected_text.slice(0, 50))}{corr.corrected_text.length > 50 ? '…' : ''}
                              </span>
                            )}
                          </p>
                          {commentMode === 'detailed' && corr.explanation && (() => {
                            const isExpanded = expandedIds.has(corr.id)
                            const isLong = corr.explanation.length > 130
                            return (
                              <div>
                                <p className={`text-xs text-slate-400 leading-snug${!isExpanded && isLong ? ' line-clamp-2' : ''}`}>
                                  {renderText(corr.explanation)}
                                </p>
                                {isLong && (
                                  <button
                                    onClick={e => {
                                      e.stopPropagation()
                                      setExpandedIds(prev => {
                                        const next = new Set(prev)
                                        if (isExpanded) next.delete(corr.id); else next.add(corr.id)
                                        return next
                                      })
                                    }}
                                    className="text-[10px] text-indigo-400 hover:text-indigo-600 transition-colors mt-0.5 leading-none"
                                  >
                                    {isExpanded ? '▲ Réduire' : '▼ Voir tout'}
                                  </button>
                                )}
                              </div>
                            )
                          })()}
                          {isH && corr.source && (
                            <p className="text-[10px] leading-snug mt-0.5 flex items-start gap-0.5">
                              <span className="text-amber-500 shrink-0">🔗</span>
                              {corr.source.startsWith('http') ? (
                                <a
                                  href={corr.source}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  onClick={e => e.stopPropagation()}
                                  className="text-amber-600 hover:text-amber-800 underline truncate"
                                >
                                  {corr.source.length > 60 ? corr.source.slice(0, 60) + '…' : corr.source}
                                </a>
                              ) : (
                                <span className="text-amber-600 truncate">Vérifié via {corr.source}</span>
                              )}
                            </p>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}

                {hiddenCount > 0 && (
                  <div className="px-4 py-3 text-center text-xs text-slate-400">
                    et {hiddenCount} correction{hiddenCount > 1 ? 's' : ''} supplémentaire{hiddenCount > 1 ? 's' : ''}…
                  </div>
                )}

                {/* ── Section corrections non localisées ── */}
                {unlocatedCorrections.length > 0 && (
                  <>
                    {/* En-tête de section avec toggle */}
                    <div className="sticky top-0 z-10 px-3 py-2 bg-orange-50 border-t border-b border-orange-200 flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className="text-[10px] font-semibold text-orange-600 uppercase tracking-wide whitespace-nowrap">
                          Position PDF inconnue
                        </span>
                        <span className="inline-flex items-center justify-center rounded-full bg-orange-200 text-orange-700 text-[10px] font-bold px-1.5 py-px min-w-[18px]">
                          {unlocatedCorrections.length}
                        </span>
                      </div>
                      <button
                        onClick={() => setShowUnlocated(s => !s)}
                        className="text-[10px] text-slate-500 hover:text-indigo-600 transition-colors underline whitespace-nowrap shrink-0"
                      >
                        {showUnlocated ? 'Masquer' : 'Afficher'}
                      </button>
                    </div>

                    {/* Liste des corrections non localisées */}
                    {showUnlocated && displayedUnlocated.map(corr => {
                      const meta = CATEGORY_META[corr.category]
                      const isH = corr.category === 'H'
                      const isActive = activeCorrId === corr.id
                      const isPinned = pinnedIds.has(corr.id)
                      const isLiked = likedIds.has(corr.id)
                      return (
                        <div
                          key={corr.id}
                          data-corr-id={corr.id}
                          onClick={() => selectCorrection(corr)}
                          className={[
                            'px-3 py-2.5 cursor-pointer transition-all group border-l-2',
                            pinFlashedId === corr.id ? 'pin-flash' : '',
                            isActive
                              ? 'bg-indigo-50 border-l-indigo-400'
                              : isLiked
                              ? 'bg-rose-50/80 border-l-rose-200 hover:bg-rose-100/80'
                              : isPinned
                              ? 'bg-amber-50/80 border-l-amber-200 hover:bg-amber-100/80'
                              : 'bg-orange-50/70 border-l-orange-200 hover:bg-orange-100/70',
                          ].join(' ')}
                        >
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="text-[10px] font-mono font-semibold text-slate-400 shrink-0">p.{corr.page}</span>
                            {meta && (
                              <span className={`text-[10px] font-medium rounded-full px-1.5 py-px ${meta.bg} ${meta.color}`}>
                                {meta.label}
                              </span>
                            )}
                            {corr.confidence && !isH && (
                              <span className={`text-[10px] font-medium rounded-full px-1.5 py-px ${CONFIDENCE_COLOR[corr.confidence] || 'bg-slate-100 text-slate-600'}`}>
                                {corr.confidence}
                              </span>
                            )}
                            {conflictIds.has(corr.id) && (
                              <span className="text-[10px] font-medium rounded-full px-1.5 py-px bg-orange-100 text-orange-700" title="Correction contradictoire">⚡ Conflit</span>
                            )}
                            <span className="text-[10px] text-orange-400 italic ml-auto shrink-0" title="Texte introuvable dans le PDF — vérification manuelle">⊘ non localisé</span>
                            {/* Actions au survol */}
                            <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 ml-1">
                              <button onClick={e => { e.stopPropagation(); window.open(`https://www.google.com/search?q=${encodeURIComponent(corr.original_text)}`, '_blank', 'noopener,noreferrer') }} className="rounded p-1 border border-transparent hover:border-blue-200 hover:bg-blue-50 transition-colors" title="Vérifier sur Google">
                                <svg viewBox="0 0 24 24" width="13" height="13" xmlns="http://www.w3.org/2000/svg"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/></svg>
                              </button>
                              <button onClick={e => { e.stopPropagation(); handleCopy(corr) }} className="rounded px-1.5 py-1 text-xs text-slate-400 hover:text-slate-600 border border-transparent hover:border-slate-200 hover:bg-slate-50" title="Copier">{copiedId === corr.id ? '✓' : '⧉'}</button>
                              <button
                                onClick={e => { e.stopPropagation(); toggleLike(corr.id) }}
                                className={`rounded px-1.5 py-1 text-xs border border-transparent transition-colors ${isLiked ? 'text-rose-500 hover:bg-rose-50 hover:border-rose-200' : 'text-slate-400 hover:text-rose-500 hover:bg-rose-50 hover:border-rose-200'}`}
                                title={isLiked ? 'Retirer le favori' : 'Marquer comme pertinent'}
                              >{isLiked ? '❤️' : '🤍'}</button>
                              <button onClick={e => { e.stopPropagation(); openFeedbackModal(corr) }} className="rounded px-1.5 py-1 text-xs text-red-400 hover:text-red-600 border border-transparent hover:border-red-200 hover:bg-red-50" title="Faux positif">👎</button>
                            </div>
                          </div>
                          <div className="mt-1 space-y-0.5">
                            {corr.description && (
                              <p className={`text-[11px] font-semibold uppercase tracking-wide truncate ${isH ? 'text-amber-700' : 'text-slate-500'}`}>
                                {corr.description}
                              </p>
                            )}
                            <p className="text-[14px] leading-snug">
                              <span className={isH ? 'text-amber-800 bg-amber-100 rounded px-0.5' : 'line-through text-red-400'}>
                                {corr.original_text.slice(0, 70)}{corr.original_text.length > 70 ? '…' : ''}
                              </span>
                              {corr.corrected_text && !isH && corr.corrected_text !== corr.original_text && (
                                <span className="text-green-700 font-medium">
                                  {' → '}{corr.corrected_text.slice(0, 50)}{corr.corrected_text.length > 50 ? '…' : ''}
                                </span>
                              )}
                            </p>
                            {commentMode === 'detailed' && corr.explanation && (() => {
                              const isExpanded = expandedIds.has(corr.id)
                              const isLong = corr.explanation.length > 130
                              return (
                                <div>
                                  <p className={`text-xs text-slate-400 leading-snug${!isExpanded && isLong ? ' line-clamp-2' : ''}`}>
                                    {renderText(corr.explanation)}
                                  </p>
                                  {isLong && (
                                    <button
                                      onClick={e => {
                                        e.stopPropagation()
                                        setExpandedIds(prev => {
                                          const next = new Set(prev)
                                          if (isExpanded) next.delete(corr.id); else next.add(corr.id)
                                          return next
                                        })
                                      }}
                                      className="text-[10px] text-indigo-400 hover:text-indigo-600 transition-colors mt-0.5 leading-none"
                                    >
                                      {isExpanded ? '▲ Réduire' : '▼ Voir tout'}
                                    </button>
                                  )}
                                </div>
                              )
                            })()}
                            {isH && corr.source && (
                              <p className="text-[10px] leading-snug mt-0.5 flex items-start gap-0.5">
                                <span className="text-amber-500 shrink-0">🔗</span>
                                {corr.source.startsWith('http')
                                  ? <a href={corr.source} target="_blank" rel="noopener noreferrer" onClick={e => e.stopPropagation()} className="text-amber-600 hover:text-amber-800 underline truncate">{corr.source.length > 60 ? corr.source.slice(0, 60) + '…' : corr.source}</a>
                                  : <span className="text-amber-600 truncate">Vérifié via {corr.source}</span>
                                }
                              </p>
                            )}
                          </div>
                        </div>
                      )
                    })}
                  </>
                )}
              </div>

              {/* ── Barre multi-sélection ─────────────────────────────────── */}
              {selectedIds.size > 0 && (
                <div className="shrink-0 border-t border-stone-200 bg-white px-4 py-2.5 flex items-center gap-3">
                  <span className="text-xs text-stone-600 font-medium flex-1">
                    {selectedIds.size} correction{selectedIds.size > 1 ? 's' : ''} sélectionnée{selectedIds.size > 1 ? 's' : ''}
                  </span>
                  <button
                    onClick={clearSelection}
                    className="text-[11px] text-stone-400 hover:text-stone-600 transition-colors px-2 py-1"
                  >
                    Annuler
                  </button>
                  <button
                    onClick={handleBulkFP}
                    disabled={bulkFpLoading}
                    className="text-[11px] rounded-lg bg-red-50 border border-red-200 px-3 py-1.5 text-red-600 hover:bg-red-100 font-medium transition-colors disabled:opacity-50 flex items-center gap-1.5"
                  >
                    {bulkFpLoading ? (
                      <svg className="h-3 w-3 animate-spin" viewBox="0 0 24 24" fill="none">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                      </svg>
                    ) : '👎'}
                    Faux positifs
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Add correction modal ───────────────────────────────────────────── */}
      {addCorrFormOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60">
          <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl overflow-hidden">
            <div className="px-5 py-4 border-b border-slate-100 flex items-center gap-2">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-indigo-100">
                <svg className="h-4 w-4 text-indigo-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
              </div>
              <div className="flex-1">
                <p className="text-sm font-semibold text-slate-800">Signaler une erreur</p>
                <p className="text-[11px] text-slate-400">Page {addCorrPage} · correction manuelle</p>
              </div>
              <button
                onClick={() => setAddCorrFormOpen(false)}
                className="h-7 w-7 flex items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="px-5 py-4 space-y-3">
              {/* Category */}
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Catégorie</label>
                <select
                  value={addCorrData.category}
                  onChange={e => setAddCorrData(d => ({ ...d, category: e.target.value }))}
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                >
                  {Object.entries(CATEGORY_META).map(([cat, m]) => (
                    <option key={cat} value={cat}>{cat} – {m.label}</option>
                  ))}
                </select>
              </div>
              {/* Original text */}
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Texte original (mot ou expression à corriger)</label>
                <input
                  type="text"
                  value={addCorrData.original_text}
                  onChange={e => setAddCorrData(d => ({ ...d, original_text: e.target.value }))}
                  placeholder="ex : « il partis »"
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  autoFocus
                />
              </div>
              {/* Corrected text */}
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Correction proposée <span className="text-slate-400">(optionnel)</span></label>
                <input
                  type="text"
                  value={addCorrData.corrected_text}
                  onChange={e => setAddCorrData(d => ({ ...d, corrected_text: e.target.value }))}
                  placeholder="ex : « il partit »"
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
              {/* Description */}
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Note <span className="text-slate-400">(optionnel)</span></label>
                <input
                  type="text"
                  value={addCorrData.description}
                  onChange={e => setAddCorrData(d => ({ ...d, description: e.target.value }))}
                  placeholder="ex : accord du participe passé"
                  className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
            </div>
            <div className="px-5 py-3 border-t border-slate-100 flex gap-2">
              <button
                onClick={() => setAddCorrFormOpen(false)}
                className="flex-1 rounded-xl border border-slate-200 px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50"
              >
                Annuler
              </button>
              <button
                onClick={handleAddCorrSubmit}
                disabled={!addCorrData.original_text.trim() || addCorrLoading}
                className="flex-1 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {addCorrLoading ? (
                  <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                ) : null}
                Ajouter
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Feedback modal ─────────────────────────────────────────────────── */}
      {feedbackTargetCorr && (
        <FeedbackModal
          correction={feedbackTargetCorr}
          onConfirm={handleRejectConfirm}
          onCancel={() => setFeedbackTargetCorr(null)}
        />
      )}
    </div>
  )
}
