/**
 * Injection des highlights de corrections dans le HTML mis en forme (DOCX).
 * Recherche chaque snippet dans les nœuds texte du HTML et l'entoure d'un <mark>.
 */
import type { Correction } from './types'

// Classes Tailwind statiques (nécessaires pour que le purge ne les supprime pas)
const CATEGORY_HIGHLIGHT: Record<string, string> = {
  orthographe: 'bg-red-100 border-b-2 border-red-400',
  grammaire: 'bg-orange-100 border-b-2 border-orange-400',
  typographie: 'bg-blue-100 border-b-2 border-blue-400',
  style: 'bg-green-100 border-b-2 border-green-400',
}

/** Décode les entités HTML courantes pour la recherche */
function decodeEntities(s: string): string {
  return s
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&nbsp;/g, '\u00A0')
}

/**
 * Injecte des <mark> autour des snippets dans le HTML.
 * Stratégie : découpe le HTML en segments texte / balises, cherche les snippets
 * dans les segments texte uniquement. Si un snippet chevauche plusieurs balises,
 * il ne sera pas mis en surbrillance (cas rare).
 */
export function injectHighlightsIntoHtml(html: string, corrections: Correction[]): string {
  // Séparer le HTML en tokens : balises et texte brut alternés
  const parts = html.split(/(<[^>]+>)/)

  for (const correction of corrections) {
    const { snippet, id, category } = correction
    if (!snippet || snippet.length === 0) continue

    const colorClass = CATEGORY_HIGHLIGHT[category] ?? CATEGORY_HIGHLIGHT.style
    const escapedSnippet = snippet.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const pattern = new RegExp(escapedSnippet)
    const patternCi = new RegExp(escapedSnippet, 'i')

    let injected = false
    for (let i = 0; i < parts.length; i++) {
      if (parts[i].startsWith('<')) continue // balise HTML, on skip

      // Décoder les entités pour la recherche
      const decoded = decodeEntities(parts[i])

      if (pattern.test(decoded) || patternCi.test(decoded)) {
        // Remplacer dans le texte décodé puis ré-encoder uniquement & et < dans le contexte hors mark
        const marked = decoded.replace(patternCi, (match) => {
          const idSafe = id.replace(/"/g, '&quot;')
          return `<mark class="correction-highlight ${colorClass} cursor-pointer rounded-sm px-0.5" data-id="${idSafe}">${match}</mark>`
        })
        // Ré-encoder les caractères spéciaux hors des balises <mark>
        parts[i] = reEncodeOutsideTags(marked)
        injected = true
        break
      }
    }

    void injected // utilisé pour éviter le warning lint
  }

  return parts.join('')
}

/**
 * Ré-encode & et < uniquement dans les nœuds texte (hors balises HTML).
 * Utilisé après avoir injecté les <mark> dans du texte décodé.
 */
function reEncodeOutsideTags(s: string): string {
  const segments = s.split(/(<[^>]+>)/)
  return segments
    .map((seg) => {
      if (seg.startsWith('<')) return seg
      return seg.replace(/&(?!amp;|lt;|gt;|quot;|#)/g, '&amp;').replace(/<(?!\/?(mark|b|i|em|strong|span|br|p|h[1-6]|ul|ol|li|blockquote)[\s>])/g, '&lt;')
    })
    .join('')
}
