import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'ÉditorIA — Correcteur professionnel IA',
  description:
    'Corriger orthographe, grammaire, typographie et style de vos documents Word et PDF avec les règles de Prolexis, propulsé par Claude.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body className="min-h-screen">{children}</body>
    </html>
  )
}
