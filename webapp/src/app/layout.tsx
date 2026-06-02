import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'ÉditorIA — Correction éditoriale PDF',
  description:
    'Correction éditoriale exhaustive de vos PDF : orthographe, grammaire, typographie, style, sémantique, faits. Annotations natives par catégorie.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body className="min-h-screen bg-slate-50">{children}</body>
    </html>
  )
}
