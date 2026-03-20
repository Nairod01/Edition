import { NextRequest, NextResponse } from 'next/server'
import { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType } from 'docx'
import type { Correction } from '@/lib/types'

export const runtime = 'nodejs'

interface ExportRequest {
  extractedText: string
  corrections: Correction[]
  filename: string
  mode: 'report' | 'corrected'
}

export async function POST(request: NextRequest) {
  try {
    const body: ExportRequest = await request.json()
    const { extractedText, corrections, filename, mode } = body

    let docBuffer: Buffer

    if (mode === 'corrected') {
      docBuffer = await buildCorrectedDoc(extractedText, corrections, filename)
    } else {
      docBuffer = await buildReportDoc(corrections, filename)
    }

    const outputName =
      mode === 'corrected'
        ? `${stripExt(filename)}_corrigé.docx`
        : `rapport_corrections_${stripExt(filename)}.docx`

    return new NextResponse(new Uint8Array(docBuffer), {
      headers: {
        'Content-Type':
          'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(outputName)}`,
      },
    })
  } catch (err) {
    return NextResponse.json(
      { error: `Erreur lors de la génération : ${err instanceof Error ? err.message : String(err)}` },
      { status: 500 }
    )
  }
}

/** Génère un document avec le texte corrigé */
async function buildCorrectedDoc(
  text: string,
  corrections: Correction[],
  filename: string
): Promise<Buffer> {
  // Appliquer les corrections de la fin vers le début pour ne pas décaler les positions
  let correctedText = text
  for (const correction of corrections) {
    // Remplacement simple (première occurrence)
    correctedText = correctedText.replace(correction.snippet, correction.corrected)
  }

  const paragraphs = correctedText
    .split(/\n{2,}/)
    .flatMap((para) => {
      // Diviser les paragraphes longs sur les retours à la ligne simples
      const lines = para.split('\n').filter((l) => l.trim())
      return lines.map(
        (line) =>
          new Paragraph({
            children: [new TextRun({ text: line, size: 24 })],
            spacing: { after: 200 },
          })
      )
    })

  const doc = new Document({
    sections: [
      {
        properties: {},
        children: [
          new Paragraph({
            text: `Document corrigé — ${filename}`,
            heading: HeadingLevel.HEADING_1,
            spacing: { after: 400 },
          }),
          new Paragraph({
            children: [
              new TextRun({
                text: `${corrections.length} correction(s) appliquée(s) automatiquement`,
                italics: true,
                color: '666666',
                size: 20,
              }),
            ],
            spacing: { after: 600 },
          }),
          ...paragraphs,
        ],
      },
    ],
  })

  return Buffer.from(await Packer.toBuffer(doc))
}

const CATEGORY_CONFIG: Record<string, { label: string; color: string }> = {
  orthographe: { label: 'Orthographe', color: 'C0392B' },
  grammaire: { label: 'Grammaire', color: 'D35400' },
  typographie: { label: 'Typographie', color: '2471A3' },
  style: { label: 'Style & Syntaxe', color: '1E8449' },
  coherence: { label: 'Cohérence', color: '7D3C98' },
  renvoi: { label: 'Renvois de page', color: 'B7950B' },
}

/** Génère un rapport de corrections formaté */
async function buildReportDoc(corrections: Correction[], filename: string): Promise<Buffer> {
  const byCategory = groupBy(corrections, (c) => c.category)
  const categoryOrder: Correction['category'][] = [
    'orthographe',
    'grammaire',
    'typographie',
    'style',
    'coherence',
    'renvoi',
  ]

  const children: Paragraph[] = [
    // Titre
    new Paragraph({
      text: 'Rapport de corrections linguistiques',
      heading: HeadingLevel.HEADING_1,
      alignment: AlignmentType.CENTER,
      spacing: { after: 200 },
    }),
    new Paragraph({
      children: [new TextRun({ text: `Document : ${filename}`, italics: true, size: 22 })],
      alignment: AlignmentType.CENTER,
      spacing: { after: 120 },
    }),
    new Paragraph({
      children: [
        new TextRun({
          text: `Total : ${corrections.length} correction(s)`,
          bold: true,
          size: 22,
        }),
      ],
      alignment: AlignmentType.CENTER,
      spacing: { after: 600 },
    }),
  ]

  for (const category of categoryOrder) {
    const items = byCategory[category]
    if (!items || items.length === 0) continue

    const config = CATEGORY_CONFIG[category]

    // En-tête de catégorie
    children.push(
      new Paragraph({
        children: [
          new TextRun({
            text: `${config.label} (${items.length})`,
            bold: true,
            size: 28,
            color: config.color,
          }),
        ],
        heading: HeadingLevel.HEADING_2,
        spacing: { before: 400, after: 200 },
        border: {
          bottom: { color: config.color, size: 6, space: 1, style: 'single' },
        },
      })
    )

    for (let i = 0; i < items.length; i++) {
      const c = items[i]

      // Numéro + original → corrigé
      children.push(
        new Paragraph({
          children: [
            new TextRun({ text: `${i + 1}. `, bold: true, size: 22 }),
            new TextRun({ text: c.snippet, strike: true, color: 'C0392B', size: 22 }),
            new TextRun({ text: '  →  ', size: 22, color: '888888' }),
            new TextRun({ text: c.corrected, bold: true, color: '1E8449', size: 22 }),
          ],
          spacing: { before: 200, after: 80 },
        })
      )

      // Règle
      children.push(
        new Paragraph({
          children: [
            new TextRun({ text: 'Règle : ', bold: true, size: 20, color: '444444' }),
            new TextRun({ text: c.rule, size: 20, color: '444444' }),
          ],
          spacing: { after: 60 },
          indent: { left: 360 },
        })
      )

      // Explication
      children.push(
        new Paragraph({
          children: [new TextRun({ text: c.explanation, size: 20, italics: true, color: '666666' })],
          spacing: { after: 200 },
          indent: { left: 360 },
        })
      )

      // Contexte
      if (c.context) {
        children.push(
          new Paragraph({
            children: [
              new TextRun({ text: 'Contexte : ', bold: true, size: 18, color: '888888' }),
              new TextRun({ text: `«\u00a0${c.context}\u00a0»`, size: 18, color: '888888' }),
            ],
            spacing: { after: 240 },
            indent: { left: 360 },
          })
        )
      }
    }
  }

  const doc = new Document({ sections: [{ properties: {}, children }] })
  return Buffer.from(await Packer.toBuffer(doc))
}

function groupBy<T>(items: T[], key: (item: T) => string): Record<string, T[]> {
  return items.reduce(
    (acc, item) => {
      const k = key(item)
      if (!acc[k]) acc[k] = []
      acc[k].push(item)
      return acc
    },
    {} as Record<string, T[]>
  )
}

function stripExt(filename: string): string {
  return filename.replace(/\.[^.]+$/, '')
}
