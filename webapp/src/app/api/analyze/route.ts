import { NextRequest } from 'next/server'
import Anthropic from '@anthropic-ai/sdk'
import { extractText } from '@/lib/extractor'
import type { Correction, AnalysisResult } from '@/lib/types'

export const maxDuration = 60
export const runtime = 'nodejs'

const SYSTEM_PROMPT = `Correcteur professionnel de français (niveau éditeur). Détecte TOUTES les erreurs réelles.

6 CATÉGORIES :
- orthographe : homophones (a/à, ou/où, ces/ses, quel/quelle…), accents, pluriels
- grammaire : accord sujet-verbe, participes passés, conjugaison, pronoms
- typographie : espaces insécables avant : ; ! ?, guillemets «\u00a0»\u00a0» (jamais " "), … (jamais ...), — (jamais -)
- style : répétition à < 50 chars, phrase > 40 mots, anglicisme, pléonasme
- coherence : même entité (personne, lieu, titre, terme) écrite différemment dans le texte — signale CHAQUE occurrence incohérente (snippet=forme utilisée ici, corrected=forme de référence à adopter partout)
- renvoi : référence de page incomplète ou manquante (ex: "voir page", "cf. p.", "p. X", "→ p." sans numéro, ou numéro manifestement placeholder comme 0 ou 000)

CHAMPS JSON :
- snippet : texte EXACT verbatim (≤ 60 chars)
- context : phrase contenant l'erreur (≤ 120 chars)
- corrected : snippet corrigé uniquement
- category : orthographe|grammaire|typographie|style|coherence|renvoi
- rule : nom court de la règle
- explanation : 1 phrase concise
- severity : error|warning|suggestion`

function buildUserPrompt(text: string, chunkInfo?: string): string {
  return `Analyse${chunkInfo ? ` (${chunkInfo})` : ''} et retourne UNIQUEMENT du JSON valide :
{"corrections":[{"snippet":"…","context":"…","corrected":"…","category":"…","rule":"…","explanation":"…","severity":"…"}]}
Si aucune erreur : {"corrections":[]}

TEXTE :
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

        // Parser le formulaire ou le JSON (texte pré-extrait côté client)
        const contentType = request.headers.get('content-type') ?? ''

        let extractedText: string
        let formattedHtml: string | undefined
        let pageOffsets: number[] = []

        if (contentType.includes('application/json')) {
          // PDF pré-extrait côté client — seul le texte est envoyé
          const body = await request.json()
          if (!body.text || typeof body.text !== 'string') {
            send({ type: 'error', message: 'Payload JSON invalide : champ "text" manquant.' })
            return
          }
          extractedText = body.text
          pageOffsets = Array.isArray(body.pageOffsets) ? body.pageOffsets : []
        } else {
          // Fichier DOCX envoyé en multipart
          const formData = await request.formData()
          const file = formData.get('file') as File | null

          if (!file) {
            send({ type: 'error', message: 'Aucun fichier reçu.' })
            return
          }

          const name = file.name.toLowerCase()
          if (!name.endsWith('.docx') && !name.endsWith('.doc')) {
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
        const MAX_CHUNK = 35_000 // ~9K tokens — moins de chunks = tout en 1 batch parallèle
        const chunks = chunkText(extractedText, MAX_CHUNK, 300)

        let allRaw: Omit<Correction, 'id'>[] = []

        send({
          type: 'progress',
          message:
            chunks.length > 1
              ? `Analyse linguistique — ${chunks.length} parties en parallèle…`
              : 'Analyse linguistique en cours…',
          percent: 30,
        })

        // Analyse les parties avec concurrence limitée pour éviter les rate limits API
        const CONCURRENCY = 5
        let completed = 0

        async function runWithConcurrency<T>(
          items: string[],
          fn: (item: string, i: number) => Promise<T>
        ): Promise<T[]> {
          const results: T[] = new Array(items.length)
          let nextIndex = 0

          async function worker() {
            while (nextIndex < items.length) {
              const i = nextIndex++
              results[i] = await fn(items[i], i)
            }
          }

          const workers = Array.from({ length: Math.min(CONCURRENCY, items.length) }, worker)
          await Promise.all(workers)
          return results
        }

        const chunkResults = await runWithConcurrency(chunks, async (chunk, i) => {
          const label = chunks.length > 1 ? `partie ${i + 1}/${chunks.length}` : undefined
          const result = await analyzeChunk(client, chunk, label)
          completed++
          const percent = 30 + Math.round((completed / chunks.length) * 55)
          send({
            type: 'progress',
            message: `Analyse linguistique — ${completed}/${chunks.length} parties traitées…`,
            percent,
          })
          return result
        })
        for (const res of chunkResults) allRaw.push(...res)

        send({ type: 'progress', message: 'Finalisation…', percent: 95 })

        // Détection regex des renvois de page manquants (sans appel API)
        allRaw.push(...detectMissingPageRefs(extractedText))

        // Dédoublonnage
        allRaw = deduplicateCorrections(allRaw)

        // Trier par ordre d'apparition dans le texte, puis attribuer les IDs
        const withPos = allRaw.map((c) => ({
          ...c,
          _pos: findSnippetPosition(extractedText, c.snippet, c.context) ?? Infinity,
        }))
        withPos.sort((a, b) => a._pos - b._pos)

        const corrections: Correction[] = withPos.map((c, i) => {
          const pos = c._pos === Infinity ? null : c._pos
          const pageNum = pos !== null ? getPageForPosition(pos, pageOffsets) : undefined
          // eslint-disable-next-line @typescript-eslint/no-unused-vars
          const { _pos, ...rest } = c
          return { ...rest, id: `c${i + 1}`, pageNum }
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
  const MAX_RETRIES = 2
  let lastErr: unknown

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      const response = await client.messages.create({
        model: 'claude-haiku-4-5-20251001',
        max_tokens: 2048,
        system: SYSTEM_PROMPT,
        messages: [{ role: 'user', content: buildUserPrompt(text, chunkLabel) }],
      })

      const textBlock = response.content.find((b) => b.type === 'text')
      if (!textBlock || textBlock.type !== 'text') return []

      console.log(`[${chunkLabel ?? 'chunk'}] text_in (first 200):`, text.slice(0, 200).replace(/\n/g, '↵'))
      console.log(`[${chunkLabel ?? 'chunk'}] stop_reason=${response.stop_reason} tokens_out=${response.usage.output_tokens}`)
      console.log(`[${chunkLabel ?? 'chunk'}] raw response (first 500):`, textBlock.text.slice(0, 500))

      return parseCorrections(textBlock.text)
    } catch (err) {
      lastErr = err
      // Rate limit (429) : attendre avant de réessayer
      const isRateLimit =
        err instanceof Error &&
        (err.message.includes('rate') || err.message.includes('429') || err.message.includes('overloaded'))
      if (isRateLimit && attempt < MAX_RETRIES) {
        await new Promise((r) => setTimeout(r, (attempt + 1) * 3000))
        continue
      }
      break
    }
  }

  console.error(`Erreur analyse chunk${chunkLabel ? ` (${chunkLabel})` : ''}:`, lastErr)
  // Remonter l'erreur pour que l'utilisateur soit informé
  throw lastErr
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
  const valid = ['orthographe', 'grammaire', 'typographie', 'style', 'coherence', 'renvoi'] as const
  return (valid as readonly unknown[]).includes(cat) ? (cat as Correction['category']) : 'style'
}

function validateSeverity(sev: unknown): Correction['severity'] {
  const valid = ['error', 'warning', 'suggestion'] as const
  return (valid as readonly unknown[]).includes(sev) ? (sev as Correction['severity']) : 'warning'
}

/** Découpe le texte en chunks en respectant les frontières de paragraphes */
function chunkText(text: string, maxSize: number, overlap = 500): string[] {
  if (text.length <= maxSize) return [text]

  const chunks: string[] = []
  let start = 0
  const OVERLAP = overlap

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

/**
 * Détection regex des renvois de page manquants/incomplets.
 * Exemples ciblés : "voir page X", "cf. p.", "→ p.", "(p. 0)", "page 000"
 */
function detectMissingPageRefs(text: string): Omit<Correction, 'id'>[] {
  const results: Omit<Correction, 'id'>[] = []
  const seen = new Set<string>()

  // Patterns : renvoi sans numéro ou avec placeholder évident
  const patterns = [
    // "voir page", "voir p.", "cf. p.", "→ p.", "see page" sans chiffre qui suit
    /\b(voir\s+(?:la\s+)?page|cf\.?\s+p\.?|→\s*p\.?|see\s+page)\s*(?!\d)/gi,
    // "page X" ou "p. X" où X est 0, 000, XX, ou absent
    /\b(?:page|p\.)\s+(?:0+|X+|xx+|\?+)\b/gi,
    // "(p. )" avec espace puis parenthèse — numéro manquant
    /\(p\.\s*\)/gi,
    // "renvoi" seul ou suivi de p. sans numéro
    /\brenvoi\s+(?:(?:à\s+la\s+)?page\s*(?!\d)|p\.?\s*(?!\d))/gi,
  ]

  for (const regex of patterns) {
    let m: RegExpExecArray | null
    while ((m = regex.exec(text)) !== null) {
      const snippet = m[0].slice(0, 60)
      const key = snippet.toLowerCase().trim()
      if (seen.has(key)) continue
      seen.add(key)

      const ctxStart = Math.max(0, m.index - 40)
      const ctxEnd = Math.min(text.length, m.index + snippet.length + 40)
      const context = text.slice(ctxStart, ctxEnd).replace(/\n+/g, ' ').trim()

      results.push({
        snippet,
        context,
        corrected: snippet + ' [N°]',
        category: 'renvoi',
        rule: 'Renvoi de page manquant',
        explanation: 'Le numéro de page de ce renvoi est absent ou incomplet — à compléter avant publication.',
        severity: 'warning',
      })
    }
  }

  return results
}
