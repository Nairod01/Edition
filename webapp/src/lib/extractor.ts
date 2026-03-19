/**
 * Extraction de texte côté serveur uniquement.
 * Supporte .docx (via mammoth) et .pdf (via pdf-parse).
 */

export interface ExtractionResult {
  /** Texte brut pour l'analyse par Claude */
  text: string
  /** HTML mis en forme (DOCX uniquement, pour l'affichage) */
  formattedHtml?: string
  /** Offset de caractère dans `text` où commence chaque page (PDF uniquement) */
  pageOffsets: number[]
}

export async function extractText(file: File): Promise<ExtractionResult> {
  const buffer = Buffer.from(await file.arrayBuffer())
  const name = file.name.toLowerCase()

  if (name.endsWith('.docx') || name.endsWith('.doc')) {
    return extractDocx(buffer)
  }

  if (name.endsWith('.pdf')) {
    return extractPdf(buffer)
  }

  throw new Error(`Format non supporté : ${file.name}. Utilisez un fichier .docx ou .pdf.`)
}

async function extractDocx(buffer: Buffer): Promise<ExtractionResult> {
  const mammoth = await import('mammoth')

  // Texte brut pour l'analyse
  const textResult = await mammoth.default.extractRawText({ buffer })
  const errors = textResult.messages.filter((m: any) => m.type === 'error')
  if (errors.length > 0 && !textResult.value.trim()) {
    throw new Error('Impossible de lire le fichier Word : ' + errors[0].message)
  }

  // HTML mis en forme pour l'affichage (italique, gras, titres…)
  let formattedHtml: string | undefined
  try {
    const htmlResult = await mammoth.default.convertToHtml({ buffer })
    if (htmlResult.value.trim()) formattedHtml = htmlResult.value
  } catch {
    // Si l'export HTML échoue, on continue avec le texte brut
  }

  return { text: textResult.value, formattedHtml, pageOffsets: [] }
}

async function extractPdf(buffer: Buffer): Promise<ExtractionResult> {
  const { default: pdfParse } = await import('pdf-parse/lib/pdf-parse.js')

  const pageTexts: string[] = []

  // Rendu personnalisé pour collecter le texte page par page
  async function pagerender(pageData: any): Promise<string> {
    try {
      const textContent = await pageData.getTextContent({
        normalizeWhitespace: false,
        disableCombineTextItems: false,
      })
      let lastY: number | undefined
      let text = ''
      for (const item of textContent.items as any[]) {
        if (lastY === undefined || lastY === item.transform[5]) {
          text += item.str
        } else {
          text += '\n' + item.str
        }
        lastY = item.transform[5]
      }
      pageTexts.push(text)
      return text
    } catch {
      pageTexts.push('')
      return ''
    }
  }

  try {
    await pdfParse(buffer, { pagerender })

    if (pageTexts.length === 0) {
      // Fallback vers l'extraction standard
      const data = await pdfParse(buffer)
      return { text: data.text, pageOffsets: [] }
    }

    // Construire le texte complet en traçant les offsets de pages
    const PAGE_SEP = '\n\n'
    const pageOffsets: number[] = []
    let offset = 0
    const parts: string[] = []

    for (let i = 0; i < pageTexts.length; i++) {
      pageOffsets.push(offset)
      parts.push(pageTexts[i])
      offset += pageTexts[i].length
      if (i < pageTexts.length - 1) offset += PAGE_SEP.length
    }

    return { text: parts.join(PAGE_SEP), pageOffsets }
  } catch (err) {
    throw new Error('Impossible de lire le fichier PDF : ' + String(err))
  }
}
