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

  // Worker en CDN — pas de bundling nécessaire
  pdfjsLib.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjsLib.version}/build/pdf.worker.min.mjs`

  const arrayBuffer = await file.arrayBuffer()
  const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise

  const pageTexts: string[] = []
  const PAGE_SEP = '\n\n'

  for (let i = 1; i <= pdf.numPages; i++) {
    if (onProgress) onProgress(i, pdf.numPages)
    const page = await pdf.getPage(i)
    const textContent = await page.getTextContent()

    let lastY: number | undefined
    let text = ''
    for (const item of textContent.items as any[]) {
      if ('str' in item) {
        if (lastY === undefined || lastY === item.transform[5]) {
          text += item.str
        } else {
          text += '\n' + item.str
        }
        lastY = item.transform[5]
      }
    }
    pageTexts.push(text)
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
