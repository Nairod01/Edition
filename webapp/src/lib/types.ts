export type Category = 'orthographe' | 'grammaire' | 'typographie' | 'style'
export type Severity = 'error' | 'warning' | 'suggestion'

export interface Correction {
  id: string
  /** Texte exact fautif tel qu'il apparaît dans le document */
  snippet: string
  /** Phrase complète contenant l'erreur (pour localisation) */
  context: string
  /** Version corrigée du snippet */
  corrected: string
  category: Category
  /** Nom court de la règle (ex: "Accord sujet-verbe") */
  rule: string
  /** Explication pédagogique de la règle */
  explanation: string
  severity: Severity
  /** Numéro de page dans le document original (PDF uniquement) */
  pageNum?: number
}

export interface AnalysisResult {
  corrections: Correction[]
  extractedText: string
  /** HTML mis en forme généré par mammoth (DOCX uniquement) */
  formattedHtml?: string
  language: 'fr' | 'en' | 'mixed'
  charCount: number
  wordCount: number
}

export interface ProgressEvent {
  type: 'progress'
  message: string
  percent: number
}

export interface ResultEvent {
  type: 'result'
  data: AnalysisResult
}

export interface ErrorEvent {
  type: 'error'
  message: string
}

export type StreamEvent = ProgressEvent | ResultEvent | ErrorEvent
