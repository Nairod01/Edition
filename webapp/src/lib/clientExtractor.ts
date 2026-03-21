/**
 * Extraction de texte PDF côté client (browser) via pdfjs-dist.
 * Évite d'envoyer le fichier brut au serveur (limite 4.5MB Vercel Hobby).
 */

export interface ClientExtractionResult {
  text: string
  pageOffsets: number[]
}

export async function extractPdfClientSide(
  file: File,
  onProgress?: (page: number, total: number) => void
): Promise<ClientExtractionResult> {
  const pdfjsLib = await import('pdfjs-dist')

  // Worker local (public/) — évite le téléchargement CDN lent
  pdfjsLib.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs'

  const arrayBuffer = await file.arrayBuffer()
  const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise

  const PAGE_SEP = '\n\n'
  let completed = 0

  // Extraction parallèle des pages (au lieu de séquentielle)
  const BATCH = 8
  const pageTexts: string[] = new Array(pdf.numPages)

  for (let batchStart = 1; batchStart <= pdf.numPages; batchStart += BATCH) {
    const batchEnd = Math.min(batchStart + BATCH - 1, pdf.numPages)
    await Promise.all(
      Array.from({ length: batchEnd - batchStart + 1 }, async (_, bi) => {
        const i = batchStart + bi
        const page = await pdf.getPage(i)
        const textContent = await page.getTextContent()

        let lastY: number | undefined
        let text = ''
        for (const item of textContent.items as any[]) {
          if ('str' in item) {
            if (lastY === undefined || lastY === item.transform[5]) {
              if (text.length > 0 && !text.endsWith(' ') && item.str.length > 0 && !item.str.startsWith(' ')) {
                text += ' '
              }
              text += item.str
            } else {
              text += '\n' + item.str
            }
            lastY = item.transform[5]
          }
        }
        pageTexts[i - 1] = text
        completed++
        if (onProgress) onProgress(completed, pdf.numPages)
      })
    )
  }

  // Calculer les offsets de pages
  const pageOffsets: number[] = []
  let offset = 0
  for (let i = 0; i < pageTexts.length; i++) {
    pageOffsets.push(offset)
    offset += pageTexts[i].length
    if (i < pageTexts.length - 1) offset += PAGE_SEP.length
  }

  return { text: pageTexts.join(PAGE_SEP), pageOffsets }
}
