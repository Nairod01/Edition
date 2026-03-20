'use client'

import { useState } from 'react'
import type { AnalysisResult } from '@/lib/types'

interface Props {
  result: AnalysisResult
  filename: string
}

export function ExportButtons({ result, filename }: Props) {
  const [downloading, setDownloading] = useState<'report' | 'corrected' | null>(null)

  const downloadDocx = async (mode: 'report' | 'corrected') => {
    setDownloading(mode)
    try {
      const response = await fetch('/api/export-docx', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          extractedText: result.extractedText,
          corrections: result.corrections,
          filename,
          mode,
        }),
      })

      if (!response.ok) {
        const err = await response.json()
        alert('Erreur : ' + (err.error ?? 'Inconnu'))
        return
      }

      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const outputName =
        mode === 'corrected'
          ? `${stripExt(filename)}_corrigé.docx`
          : `rapport_corrections_${stripExt(filename)}.docx`
      a.download = outputName
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      alert('Erreur lors du téléchargement : ' + String(err))
    } finally {
      setDownloading(null)
    }
  }

  const printReport = () => {
    const { corrections } = result

    const categoryConfig = {
      orthographe: { label: 'Orthographe', color: '#ef4444' },
      grammaire: { label: 'Grammaire', color: '#f97316' },
      typographie: { label: 'Typographie', color: '#3b82f6' },
      style: { label: 'Style', color: '#22c55e' },
      coherence: { label: 'Cohérence', color: '#a855f7' },
      renvoi: { label: 'Renvois de page', color: '#ca8a04' },
    } as const

    const byCategory = corrections.reduce(
      (acc, c) => {
        if (!acc[c.category]) acc[c.category] = []
        acc[c.category]!.push(c)
        return acc
      },
      {} as Record<string, typeof corrections>
    )

    const categorySections = (
      ['orthographe', 'grammaire', 'typographie', 'style', 'coherence', 'renvoi'] as const
    )
      .map((cat) => {
        const items = byCategory[cat]
        if (!items || items.length === 0) return ''
        const cfg = categoryConfig[cat]
        const rows = items
          .map(
            (c, i) => `
          <div style="margin-bottom:16px;padding:12px;border-left:4px solid ${cfg.color};background:#fafafa;border-radius:4px">
            <div style="margin-bottom:6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
              <span style="font-size:13px;text-decoration:line-through;color:#ef4444;background:#fee2e2;padding:2px 6px;border-radius:3px;font-family:monospace">${escHtml(c.snippet)}</span>
              <span style="color:#aaa">→</span>
              <span style="font-size:13px;color:#166534;font-weight:600;background:#dcfce7;padding:2px 6px;border-radius:3px;font-family:monospace">${escHtml(c.corrected)}</span>
              ${c.pageNum != null ? `<span style="margin-left:auto;font-size:11px;color:#888;background:#f3f4f6;padding:1px 6px;border-radius:3px;white-space:nowrap">p.\u00a0${c.pageNum}</span>` : ''}
            </div>
            <div style="font-weight:600;color:#1a1a2e;font-size:13px;margin-bottom:4px">${escHtml(c.rule)}</div>
            <div style="font-size:12px;color:#666;line-height:1.5">${escHtml(c.explanation)}</div>
            ${c.context ? `<div style="font-size:11px;color:#999;margin-top:6px;font-style:italic">« ${escHtml(c.context)} »</div>` : ''}
          </div>
        `
          )
          .join('')

        return `
        <h2 style="color:${cfg.color};font-size:18px;margin:28px 0 12px;padding-bottom:6px;border-bottom:2px solid ${cfg.color}">
          ${cfg.label} <span style="font-weight:400;font-size:14px">(${items.length})</span>
        </h2>
        ${rows}
      `
      })
      .join('')

    const html = `<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Rapport de corrections — ${escHtml(filename)}</title>
<style>
  body { font-family: Georgia, serif; max-width: 760px; margin: 0 auto; padding: 40px 24px; color: #1a1a2e; }
  @media print { body { padding: 20px; } }
</style>
</head>
<body>
  <h1 style="font-size:24px;text-align:center;margin-bottom:6px">Rapport de corrections linguistiques</h1>
  <p style="text-align:center;color:#666;font-size:14px;margin-bottom:6px">Document : ${escHtml(filename)}</p>
  <p style="text-align:center;font-weight:600;font-size:15px;margin-bottom:36px">${corrections.length} correction(s) détectée(s)</p>
  ${categorySections}
</body>
</html>`

    const win = window.open('', '_blank')
    if (!win) return
    win.document.write(html)
    win.document.close()
    setTimeout(() => win.print(), 500)
  }

  return (
    <div className="flex gap-3 flex-wrap">
      <button
        onClick={printReport}
        className="flex items-center gap-2 px-4 py-2 bg-gray-800 text-white rounded-lg text-sm font-medium hover:bg-gray-700 transition-colors"
      >
        <span>🖨️</span>
        Rapport PDF
      </button>

      <button
        onClick={() => downloadDocx('report')}
        disabled={downloading !== null}
        className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <span>{downloading === 'report' ? '⏳' : '📥'}</span>
        {downloading === 'report' ? 'Génération…' : 'Rapport Word'}
      </button>

      <button
        onClick={() => downloadDocx('corrected')}
        disabled={downloading !== null}
        className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <span>{downloading === 'corrected' ? '⏳' : '✅'}</span>
        {downloading === 'corrected' ? 'Génération…' : 'Document corrigé (.docx)'}
      </button>
    </div>
  )
}

function stripExt(filename: string): string {
  return filename.replace(/\.[^.]+$/, '')
}

function escHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}
