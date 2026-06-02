export type JobStatus =
  | 'pending'
  | 'extracting'
  | 'awaiting_confirmation'
  | 'processing'
  | 'annotating'
  | 'done'
  | 'error'

export type DocType =
  | 'roman'
  | 'bd_comics'
  | 'jeunesse'
  | 'poesie_theatre'
  | 'documentaire'
  | 'beaux_arts'
  | 'tourisme'
  | 'cuisine'
  | 'sport'
  | 'manuel_scolaire'
  | 'parascolaire'
  | 'essai'
  | 'magazine'
  | 'revue_presse'
  | 'autre'

export interface UploadResponse {
  job_id: string
  filename: string
  pages: number
  words: number
  estimated_tokens: number
  estimated_cost_usd: number
  estimated_corrections: number
  doc_type: DocType
  status: JobStatus
  pdf_metadata?: { author: string; title: string }
}

export interface JobState {
  id: string
  filename: string
  status: JobStatus
  progress: number
  progress_label: string
  pages_count: number | null
  word_count: number | null
  estimated_cost_usd: number | null
  corrections_count: number
  corrections_by_category: Record<string, number>
  error_message: string | null
  created_at: string | null
  doc_type?: DocType
  annotated_count?: number
  h_not_annotated_count?: number
  generate_pdf?: boolean
  actual_cost_usd?: number | null
}

export interface Correction {
  id: string
  page: number
  category: string
  original_text: string
  corrected_text: string | null
  description: string
  explanation: string
  source: string
  annotation_type: string
  confidence: 'Certain' | 'Probable' | 'À vérifier'
  bbox?: { x0: number; y0: number; x1: number; y1: number } | null
  is_user_added?: boolean
  pinned?: boolean
  liked?: boolean
  is_false_positive?: boolean
}

export const CATEGORY_META: Record<
  string,
  { label: string; color: string; bg: string; border: string; dot: string }
> = {
  A: {
    label: 'Orthographe',
    color: 'text-red-700',
    bg: 'bg-red-50',
    border: 'border-red-200',
    dot: 'bg-red-400',
  },
  B: {
    label: 'Grammaire',
    color: 'text-orange-700',
    bg: 'bg-orange-50',
    border: 'border-orange-200',
    dot: 'bg-orange-400',
  },
  C: {
    label: 'Typographie',
    color: 'text-purple-700',
    bg: 'bg-purple-50',
    border: 'border-purple-200',
    dot: 'bg-purple-400',
  },
  D: {
    label: 'Syntaxe & Style',
    color: 'text-blue-700',
    bg: 'bg-blue-50',
    border: 'border-blue-200',
    dot: 'bg-blue-400',
  },
  E: {
    label: 'Sémantique',
    color: 'text-green-700',
    bg: 'bg-green-50',
    border: 'border-green-200',
    dot: 'bg-green-400',
  },
  F: {
    label: 'Uniformisation',
    color: 'text-cyan-700',
    bg: 'bg-cyan-50',
    border: 'border-cyan-200',
    dot: 'bg-cyan-400',
  },
  G: {
    label: 'Renvois',
    color: 'text-pink-700',
    bg: 'bg-pink-50',
    border: 'border-pink-200',
    dot: 'bg-pink-400',
  },
  H: {
    label: 'Vérif. des faits',
    color: 'text-amber-700',
    bg: 'bg-amber-50',
    border: 'border-amber-200',
    dot: 'bg-amber-400',
  },
}

export const DOC_TYPE_META: Record<
  DocType,
  { label: string; description: string; icon: string; group: string }
> = {
  roman: {
    label: 'Roman / Fiction',
    description: 'Littérature, nouvelles, récits',
    icon: '📖',
    group: 'Littérature',
  },
  bd_comics: {
    label: 'BD / Comics / Manga',
    description: 'Bulles, onomatopées, séquences',
    icon: '🎨',
    group: 'Littérature',
  },
  jeunesse: {
    label: 'Jeunesse / Albums',
    description: 'Livres enfants, albums illustrés',
    icon: '🌈',
    group: 'Littérature',
  },
  poesie_theatre: {
    label: 'Poésie / Théâtre',
    description: 'Vers, pièces, dramaturgie',
    icon: '🎭',
    group: 'Littérature',
  },
  documentaire: {
    label: 'Documentaire / Sciences',
    description: 'Vulgarisation, encyclopédies',
    icon: '🔬',
    group: 'Non-fiction',
  },
  beaux_arts: {
    label: 'Beaux-Arts / Architecture',
    description: "Livres d'art, catalogues, architecture, design, photographie",
    icon: '🎨',
    group: 'Non-fiction',
  },
  tourisme: {
    label: 'Tourisme / Voyages',
    description: 'Guides, cartes, récits de voyage',
    icon: '✈️',
    group: 'Non-fiction',
  },
  cuisine: {
    label: 'Cuisine / Gastronomie',
    description: 'Recettes, techniques, gastronomie',
    icon: '🍴',
    group: 'Non-fiction',
  },
  sport: {
    label: 'Sport / Bien-être',
    description: 'Sports, santé, coaching',
    icon: '⚽',
    group: 'Non-fiction',
  },
  manuel_scolaire: {
    label: 'Manuel scolaire',
    description: 'Exercices, consignes, pédagogie',
    icon: '📚',
    group: 'Éducatif',
  },
  parascolaire: {
    label: 'Parascolaire / Aide scolaire',
    description: 'Fiches, révisions, méthodes',
    icon: '✏️',
    group: 'Éducatif',
  },
  essai: {
    label: 'Essai / Rapport',
    description: 'Académique, professionnel, analyse',
    icon: '📄',
    group: 'Professionnel',
  },
  magazine: {
    label: 'Magazine / Presse',
    description: 'Articles, reportages, rubriques',
    icon: '📰',
    group: 'Presse',
  },
  revue_presse: {
    label: 'Revue / Journal',
    description: 'Périodiques, journaux, dépêches',
    icon: '🗞️',
    group: 'Presse',
  },
  autre: {
    label: 'Autre document',
    description: 'Règles éditoriales générales',
    icon: '📋',
    group: 'Professionnel',
  },
}

// ── Feedback faux positifs ─────────────────────────────────────────────────

export interface FeedbackReason {
  code: string
  label: string
  desc: string
  /** Classes Tailwind pour l'état sélectionné (couleur fixe par raison) */
  selectedClass: string
  /** Classes Tailwind pour l'icône / accent */
  iconClass: string
}

export const FEEDBACK_REASONS: FeedbackReason[] = [
  {
    code: 'already_correct',
    label: 'Déjà correct',
    desc: "L'original est correct, l'IA a inventé un problème",
    selectedClass: 'border-green-300 bg-green-50 text-green-800 ring-1 ring-green-200',
    iconClass: 'text-green-500',
  },
  {
    code: 'hallucination_text',
    label: 'Texte inventé',
    desc: "Le passage signalé n'existe pas dans le document",
    selectedClass: 'border-red-300 bg-red-50 text-red-800 ring-1 ring-red-200',
    iconClass: 'text-red-500',
  },
  {
    code: 'author_style',
    label: 'Style voulu',
    desc: "Écriture intentionnelle de l'auteur, pas une erreur",
    selectedClass: 'border-teal-300 bg-teal-50 text-teal-800 ring-1 ring-teal-200',
    iconClass: 'text-teal-500',
  },
  {
    code: 'passage_confusion',
    label: 'Confusion de passages',
    desc: "L'IA a mélangé ce texte avec un autre passage du document",
    selectedClass: 'border-purple-300 bg-purple-50 text-purple-800 ring-1 ring-purple-200',
    iconClass: 'text-purple-500',
  },
  {
    code: 'wrong_correction',
    label: 'Correction fausse',
    desc: "La suggestion proposée par l'IA est elle-même erronée",
    selectedClass: 'border-orange-300 bg-orange-50 text-orange-800 ring-1 ring-orange-200',
    iconClass: 'text-orange-500',
  },
  {
    code: 'faithful_quote',
    label: 'Citation fidèle modifiée',
    desc: "L'IA a voulu corriger une citation qui doit rester exacte",
    selectedClass: 'border-indigo-300 bg-indigo-50 text-indigo-800 ring-1 ring-indigo-200',
    iconClass: 'text-indigo-500',
  },
  {
    code: 'wrong_fact_date',
    label: 'Date ou fait incorrect',
    desc: "L'IA s'est trompée dans le fait ou la date qu'elle propose",
    selectedClass: 'border-blue-300 bg-blue-50 text-blue-800 ring-1 ring-blue-200',
    iconClass: 'text-blue-500',
  },
  {
    code: 'wrong_context',
    label: 'Mauvais contexte narratif',
    desc: "L'IA n'a pas compris le point de vue, le flashback ou l'ironie",
    selectedClass: 'border-amber-300 bg-amber-50 text-amber-800 ring-1 ring-amber-200',
    iconClass: 'text-amber-500',
  },
  {
    code: 'fictional_term',
    label: 'Terme fictif ou inventé',
    desc: "Nom, lieu ou mot inventé par l'auteur, traité comme une faute",
    selectedClass: 'border-pink-300 bg-pink-50 text-pink-800 ring-1 ring-pink-200',
    iconClass: 'text-pink-500',
  },
]

// ── Historique & Dashboard ─────────────────────────────────────────────────

export interface HistoryJobDetail {
  id: string
  filename: string
  status: string
  corrections_count: number
  false_positives_count: number
  unlocated_count: number
  doc_type: string
  created_at: string | null
  actual_cost_usd?: number | null
}

export interface DashboardData {
  total_jobs: number
  total_corrections: number
  total_false_positives: number
  total_unlocated: number
  by_reason: Record<string, number>
  by_category: Record<string, number>
  jobs: HistoryJobDetail[]
}

export type Preset = 'complete' | 'quick' | 'coherence' | 'facts'
export type CommentMode = 'simple' | 'detailed'

export interface DocumentMetadata {
  author: string
  title: string
  characters: string
  citation_lang: string
  house_rules: string
}

export const PRESET_META: Record<Preset, { label: string; desc: string; categories: string[]; icon: string; color: string }> = {
  complete: {
    label: 'Correction complète',
    desc: 'Toutes les catégories A→H',
    categories: ['A','B','C','D','E','F','G','H'],
    icon: '✦',
    color: 'indigo',
  },
  quick: {
    label: 'Correction rapide',
    desc: 'Orthographe, grammaire, typographie, syntaxe (A+B+C+D)',
    categories: ['A','B','C','D'],
    icon: '⚡',
    color: 'blue',
  },
  coherence: {
    label: 'Cohérence globale',
    desc: 'Sémantique, uniformisation, renvois (E+F+G)',
    categories: ['E','F','G'],
    icon: '◎',
    color: 'green',
  },
  facts: {
    label: 'Vérification des faits',
    desc: "Noms propres, dates, titres d'œuvres (H)",
    categories: ['H'],
    icon: '⊛',
    color: 'amber',
  },
}
