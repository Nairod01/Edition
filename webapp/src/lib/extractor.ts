/**
 * Extraction de texte côté serveur uniquement.
 * Supporte .docx (via mammoth) et .pdf (via pdf-parse).
 */

export async function extractText(file: File): Promise<string> {
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

async function extractDocx(buffer: Buffer): Promise<string> {
  // Dynamic import to avoid issues at build time
  const mammoth = await import('mammoth')
  const result = await mammoth.default.extractRawText({ buffer })
  if (result.messages.length > 0) {
    const errors = result.messages.filter((m) => m.type === 'error')
    if (errors.length > 0 && !result.value.trim()) {
      throw new Error('Impossible de lire le fichier Word : ' + errors[0].message)
    }
  }
  return result.value
}

async function extractPdf(buffer: Buffer): Promise<string> {
  // Dynamic import to avoid pdf-parse test file issues at build time
  const { default: pdfParse } = await import('pdf-parse/lib/pdf-parse.js')
  try {
    const data = await pdfParse(buffer)
    return data.text
  } catch (err) {
    throw new Error('Impossible de lire le fichier PDF : ' + String(err))
  }
}
