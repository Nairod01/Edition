'use client'

import { useCallback, useState } from 'react'

interface Props {
  onFile: (file: File) => void
  disabled?: boolean
}

export function UploadZone({ onFile, disabled }: Props) {
  const [isDragging, setIsDragging] = useState(false)
  const [selectedName, setSelectedName] = useState<string | null>(null)

  const handleFile = useCallback(
    (file: File) => {
      const name = file.name.toLowerCase()
      if (!name.endsWith('.pdf') && !name.endsWith('.docx') && !name.endsWith('.doc')) {
        alert('Format non supporté. Utilisez un fichier Word (.docx) ou PDF (.pdf).')
        return
      }
      setSelectedName(file.name)
      onFile(file)
    },
    [onFile]
  )

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(true)
  }, [])

  const onDragLeave = useCallback(() => setIsDragging(false), [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setIsDragging(false)
      const file = e.dataTransfer.files[0]
      if (file) handleFile(file)
    },
    [handleFile]
  )

  const onChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file) handleFile(file)
    },
    [handleFile]
  )

  return (
    <label
      className={`
        relative flex flex-col items-center justify-center
        w-full min-h-[220px] rounded-2xl border-2 border-dashed
        transition-all duration-200 cursor-pointer
        ${disabled ? 'opacity-50 cursor-not-allowed' : ''}
        ${
          isDragging
            ? 'border-blue-500 bg-blue-50 scale-[1.01]'
            : 'border-gray-300 bg-gray-50 hover:border-blue-400 hover:bg-blue-50/50'
        }
      `}
      onDragOver={disabled ? undefined : onDragOver}
      onDragLeave={disabled ? undefined : onDragLeave}
      onDrop={disabled ? undefined : onDrop}
    >
      <input
        type="file"
        accept=".pdf,.docx,.doc"
        className="sr-only"
        onChange={onChange}
        disabled={disabled}
      />

      {/* Icône */}
      <div className="text-5xl mb-4 select-none">
        {isDragging ? '📂' : selectedName ? '📄' : '📎'}
      </div>

      {selectedName ? (
        <p className="text-blue-600 font-semibold text-sm">{selectedName}</p>
      ) : (
        <>
          <p className="text-gray-700 font-semibold text-base mb-1">
            Glissez votre document ici
          </p>
          <p className="text-gray-400 text-sm">ou cliquez pour parcourir</p>
        </>
      )}

      <p className="text-gray-400 text-xs mt-3">Word (.docx) · PDF — max 15 Mo</p>
    </label>
  )
}
