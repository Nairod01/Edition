import { NextRequest } from 'next/server'
import Anthropic from '@anthropic-ai/sdk'
import { extractText } from '@/lib/extractor'
import type { Correction, AnalysisResult } from '@/lib/types'

export const maxDuration = 60
export const runtime = 'nodejs'

const SYSTEM_PROMPT = `Tu es un correcteur professionnel expert en langue française (et anglaise si le texte l'est), fonctionnant comme le logiciel Prolexis. Tu analyses le texte avec la rigueur d'un éditeur professionnel et identifies TOUTES les erreurs et améliorations possibles.

CATÉGORIES DE CORRECTION :
• orthographe : fautes d'orthographe, homophones (a/à, ou/où, se/ce, s'est/c'est, quel/quelle/qu'elle, ces/ses/c'est, etc.), accents manquants ou erronés, pluriels irréguliers
• grammaire : accord sujet-verbe, conjugaison (temps, modes), participes passés avec avoir ou être, pronoms, prépositions incorrectes, syntaxe
• typographie : espace manquante avant : ; ! ?, guillemets français « » vs "anglophones", tiret em — vs trait d'union -, points de suspension … vs ..., majuscule après point final, apostrophe typographique ' vs '
• style : répétitions du même mot dans un rayon de 50 caractères, phrases > 40 mots, tournures passives excessives, anglicismes, pléonasmes, niveaux de langue incohérents, clichés

RÈGLES TYPOGRAPHIQUES FRANÇAISES PRIORITAIRES :
- Espace INSÉCABLE avant : ; ! ? et après «
- Guillemets français : « texte » (avec espaces) — jamais "texte" ou "texte"
- Points de suspension : … (caractère unique U+2026) — jamais trois points séparés ...
- Tiret de dialogue : — (em dash) — jamais un simple trait d'union -
- Pas d'espace avant la virgule ni le point
- Majuscule obligatoire après . ! ? en début de phrase

CONSIGNES IMPORTANTES :
1. "snippet" = le texte EXACT fautif tel qu'il apparaît dans le document (verbatim, max 60 caractères)
2. "context" = la phrase ou le segment contenant l'erreur pour localisation (max 150 caractères)
3. "corrected" = uniquement le snippet corrigé (pas la phrase entière)
4. "rule" = nom court et précis de la règle (ex: "Accord sujet-verbe", "Homophone a/à")
5. "explanation" = explication pédagogique en 2-3 phrases claires
6. "severity" = "error" (faute évidente), "warning" (probable), "suggestion" (amélioration)
7. Ne signale que les erreurs RÉELLES, pas les choix stylistiques volontaires`

function buildUserPrompt(text: string, chunkInfo?: string): string {
  return `Analyse ce texte${chunkInfo ? ` (${chunkInfo})` : ''} et identifie TOUTES les corrections nécessaires.

Réponds UNIQUEMENT avec du JSON valide sans aucun texte avant ou après :
{"corrections":[{"snippet":"texte fautif exact","context":"phrase contenant l'erreur","corrected":"correction","category":"orthographe|grammaire|typographie|style","rule":"Nom de la règle","explanation":"Explication pédagogique en 2-3 phrases","severity":"error|warning|suggestion"}]}

Si aucune erreur détectée : {"corrections":[]}

TEXTE À ANALYSER :
---
${text}
---`
}

export async function POST(request: NextRequest) {
  const encoder = new TextEncoder()

  const stream = new ReadableStream({
    async start(controller) {
      const send = (data: object) => {
        try {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`))
        } catch {
          // Controller might be closed
        }
      }

      try {
        // Vérifier la clé API en premier
        if (!process.env.ANTHROPIC_API_KEY) {
          send({
            type: 'error',
            message:
              'Clé API Anthropic non configurée. Créez un fichier .env.local avec ANTHROPIC_API_KEY=sk-ant-...',
          })
          return
        }

        const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY })

        // Parser le formulaire
        const formData = await request.formData()
        const file = formData.get('file') as File | null

        if (!file) {
          send({ type: 'error', message: 'Aucun fichier reçu.' })
          return
        }

        const name = file.name.toLowerCase()
        if (!name.endsWith('.pdf') && !name.endsWith('.docx') && !name.endsWith('.doc')) {
          send({
            type: 'error',
            message: 'Format non supporté. Utilisez un fichier Word (.docx) ou PDF (.pdf).',
          })
          return
        }

        if (file.size > 15 * 1024 * 1024) {
          send({ type: 'error', message: 'Fichier trop volumineux (maximum 15 Mo).' })
          return
        }

        send({ type: 'progress', message: 'Extraction du texte…', percent: 10 })

        let extractedText: string
        let formattedHtml: string | undefined
        let pageOffsets: number[] = []
        try {
          const extracted = await extractText(file)
          extractedText = extracted.text
          formattedHtml = extracted.formattedHtml
          pageOffsets = extracted.pageOffsets
        } catch (err) {
          send({
            type: 'error',
            message: `Erreur d'extraction : ${err instanceof Error ? err.message : String(err)}`,
          })
          return
        }

        if (!extractedText.trim()) {
          send({
            type: 'error',
            message:
              'Le document semble vide ou ne contient pas de texte extractible (ex: PDF scanné sans OCR).',
          })
          return
        }

        send({ type: 'progress', message: 'Découpage du document…', percent: 20 })

        // Découper si le texte est très long
        const MAX_CHUNK = 80_000 // ~20K tokens — laisse de la place pour la réponse
        const chunks = chunkText(extractedText, MAX_CHUNK)

        let allRaw: Omit<Correction, 'id'>[] = []

        for (let i = 0; i < chunks.length; i++) {
          const percent = 25 + Math.round(((i + 1) / chunks.length) * 65)
          const chunkLabel =
            chunks.length > 1 ? `partie ${i + 1}/${chunks.length}` : undefined

          send({
            type: 'progress',
            message: chunkLabel
              ? `Analyse linguistique — ${chunkLabel}…`
              : 'Analyse linguistique en cours…',
            percent,
          })

          const chunkCorrections = await analyzeChunk(client, chunks[i], chunkLabel)
          allRaw.push(...chunkCorrections)
        }

        send({ type: 'progress', message: 'Finalisation…', percent: 95 })

        // Dédoublonnage
        allRaw = deduplicateCorrections(allRaw)

        // Attribution des IDs et des numéros de page
        const corrections: Correction[] = allRaw.map((c, i) => {
          const pos = findSnippetPosition(extractedText, c.snippet, c.context)
          const pageNum = pos !== null ? getPageForPosition(pos, pageOffsets) : undefined
          return { ...c, id: `c${i + 1}`, pageNum }
        })

        const wordCount = extractedText.trim().split(/\s+/).length

        const result: AnalysisResult = {
          corrections,
          extractedText,
          formattedHtml,
          pageOffsets: pageOffsets.length > 1 ? pageOffsets : undefined,
          language: detectLanguage(extractedText),
          charCount: extractedText.length,
          wordCount,
        }

        send({ type: 'result', data: result })
      } catch (err) {
        send({
          type: 'error',
          message: `Erreur inattendue : ${err instanceof Error ? err.message : String(err)}`,
        })
      } finally {
        try {
          controller.close()
        } catch {
          // Already closed
        }
      }
    },
  })

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    },
  })
}

async function analyzeChunk(
  client: Anthropic,
  text: string,
  chunkLabel?: string
): Promise<Omit<Correction, 'id'>[]> {
  try {
    const response = await client.messages.create({
      model: 'claude-opus-4-6',
      max_tokens: 16000,
      system: SYSTEM_PROMPT,
      messages: [{ role: 'user', content: buildUserPrompt(text, chunkLabel) }],
    })

    const textBlock = response.content.find((b) => b.type === 'text')
    if (!textBlock || textBlock.type !== 'text') return []

    return parseCorrections(textBlock.text)
  } catch (err) {
    console.error('Erreur analyse chunk:', err)
    return []
  }
}

function parseCorrections(raw: string): Omit<Correction, 'id'>[] {
  // Extraire le JSON même si Claude ajoute du texte parasite
  const jsonMatch = raw.match(/\{[\s\S]*\}/)
  if (!jsonMatch) return []

  try {
    const data = JSON.parse(jsonMatch[0])
    const items: unknown[] = Array.isArray(data.corrections) ? data.corrections : []

    return items
      .filter((c): c is Record<string, unknown> => typeof c === 'object' && c !== null)
      .map((c) => ({
        snippet: String(c.snippet ?? '').trim(),
        context: String(c.context ?? '').trim(),
        corrected: String(c.corrected ?? '').trim(),
        category: validateCategory(c.category),
        rule: String(c.rule ?? 'Règle générale').trim(),
        explanation: String(c.explanation ?? '').trim(),
        severity: validateSeverity(c.severity),
      }))
      .filter(
        (c) =>
          c.snippet.length > 0 &&
          c.corrected.length > 0 &&
          c.snippet !== c.corrected &&
          c.explanation.length > 0
      )
  } catch {
    return []
  }
}

function validateCategory(cat: unknown): Correction['category'] {
  const valid = ['orthographe', 'grammaire', 'typographie', 'style'] as const
  return (valid as readonly unknown[]).includes(cat) ? (cat as Correction['category']) : 'style'
}

function validateSeverity(sev: unknown): Correction['severity'] {
  const valid = ['error', 'warning', 'suggestion'] as const
  return (valid as readonly unknown[]).includes(sev) ? (sev as Correction['severity']) : 'warning'
}

/** Découpe le texte en chunks en respectant les frontières de paragraphes */
function chunkText(text: string, maxSize: number): string[] {
  if (text.length <= maxSize) return [text]

  const chunks: string[] = []
  let start = 0
  const OVERLAP = 2000

  while (start < text.length) {
    let end = Math.min(start + maxSize, text.length)

    if (end < text.length) {
      // Couper sur une frontière de paragraphe
      const paraBreak = text.lastIndexOf('\n\n', end)
      if (paraBreak > start + maxSize / 2) {
        end = paraBreak + 2
      } else {
        // Sinon couper sur une fin de phrase
        const sentBreak = text.lastIndexOf('. ', end)
        if (sentBreak > start + maxSize / 2) {
          end = sentBreak + 2
        }
      }
    }

    chunks.push(text.slice(start, end))
    start = Math.max(start + 1, end - OVERLAP)
  }

  return chunks
}

/** Supprime les doublons basés sur snippet + category */
function deduplicateCorrections(corrections: Omit<Correction, 'id'>[]): Omit<Correction, 'id'>[] {
  const seen = new Set<string>()
  return corrections.filter((c) => {
    const key = `${c.snippet.toLowerCase()}|${c.category}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

/** Trouve la position d'un snippet dans le texte (même logique que annotate.ts) */
function findSnippetPosition(text: string, snippet: string, context: string): number | null {
  if (!snippet) return null

  const ctxKey = context.slice(0, Math.min(50, context.length)).trim()
  if (ctxKey) {
    const ctxIdx = text.indexOf(ctxKey)
    if (ctxIdx !== -1) {
      const searchFrom = Math.max(0, ctxIdx - 20)
      const searchTo = Math.min(text.length, ctxIdx + context.length + 20)
      const idx = text.indexOf(snippet, searchFrom)
      if (idx !== -1 && idx < searchTo) return idx
    }
  }

  const idx = text.indexOf(snippet)
  if (idx !== -1) return idx

  const lowerIdx = text.toLowerCase().indexOf(snippet.toLowerCase())
  if (lowerIdx !== -1) return lowerIdx

  return null
}

/** Retourne le numéro de page (1-based) pour une position dans le texte */
function getPageForPosition(pos: number, pageOffsets: number[]): number | undefined {
  if (pageOffsets.length === 0) return undefined
  let page = 1
  for (let i = 0; i < pageOffsets.length; i++) {
    if (pageOffsets[i] <= pos) page = i + 1
    else break
  }
  return page
}

function detectLanguage(text: string): 'fr' | 'en' | 'mixed' {
  const sample = text.slice(0, 5000)
  const frWords = (
    sample.match(
      /\b(le|la|les|de|du|des|un|une|est|et|en|à|que|qui|pour|pas|avec|sur|dans|ce|il|elle|nous|vous|ils|elles|je|tu|son|sa|ses|leur|leurs|tout|plus|très|bien|aussi|même|comme|mais|donc|car|ni|or)\b/gi
    ) ?? []
  ).length
  const enWords = (
    sample.match(
      /\b(the|is|are|was|were|have|has|had|be|been|do|does|did|will|would|could|should|may|might|can|of|in|to|for|on|with|at|by|from|and|or|but|if|then|than|that|this|these|those)\b/gi
    ) ?? []
  ).length

  if (frWords > enWords * 2) return 'fr'
  if (enWords > frWords * 2) return 'en'
  return 'mixed'
}
