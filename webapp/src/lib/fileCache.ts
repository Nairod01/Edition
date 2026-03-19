/**
 * Stockage de fichiers dans IndexedDB.
 * Permet de restaurer la session sans que l'utilisateur ait à re-uploader son document.
 */

const DB_NAME = 'editoria_files_v1'
const STORE_NAME = 'files'
const MAX_ENTRIES = 5 // garder les 5 derniers fichiers seulement

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME)
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

/** Enregistre le fichier dans IndexedDB (écrase si la clé existe déjà) */
export async function saveFile(key: string, file: File): Promise<void> {
  try {
    const db = await openDB()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      const store = tx.objectStore(STORE_NAME)
      store.put({ file, savedAt: Date.now() }, key)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
    db.close()
  } catch {
    // IndexedDB peut échouer en navigation privée ou si désactivé
  }
}

/** Charge un fichier depuis IndexedDB. Retourne null si absent. */
export async function loadFile(key: string): Promise<File | null> {
  try {
    const db = await openDB()
    const result = await new Promise<{ file: File; savedAt: number } | undefined>(
      (resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readonly')
        const req = tx.objectStore(STORE_NAME).get(key)
        req.onsuccess = () => resolve(req.result)
        req.onerror = () => reject(req.error)
      }
    )
    db.close()
    return result?.file ?? null
  } catch {
    return null
  }
}

/** Supprime un fichier de IndexedDB */
export async function deleteFile(key: string): Promise<void> {
  try {
    const db = await openDB()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      tx.objectStore(STORE_NAME).delete(key)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
    db.close()
  } catch {
    // ignore
  }
}
