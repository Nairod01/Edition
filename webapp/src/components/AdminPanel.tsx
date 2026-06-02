'use client'

import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '@/lib/auth'

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'

interface AdminUser {
  id: string
  email: string
  name: string | null
  role: 'user' | 'admin'
  is_active: boolean
  monthly_limit_usd: number
  current_month_spend_usd: number
  credits_remaining_eur: number | null
  spent_eur: number
  limit_eur: number | null
  jobs_count: number
  created_at: string | null
  last_login_at: string | null
}

interface UserJob {
  id: string
  filename: string
  status: string
  corrections_count: number
  false_positives_count: number
  doc_type: string
  created_at: string | null
  actual_cost_eur: number | null
}

function CreditBar({ spent, limit }: { spent: number; limit: number | null }) {
  if (!limit) return <span className="text-xs text-stone-400">Illimité</span>
  const pct = Math.min(100, Math.round((spent / limit) * 100))
  const color = pct >= 90 ? 'bg-red-400' : pct >= 70 ? 'bg-amber-400' : 'bg-emerald-400'
  const textColor = pct >= 90 ? 'text-red-600' : pct >= 70 ? 'text-amber-600' : 'text-emerald-600'
  return (
    <div className="min-w-[100px]">
      <div className="flex items-center justify-between mb-1">
        <span className={`text-[11px] font-semibold tabular-nums ${textColor}`}>
          {spent.toFixed(2)}€ / {limit.toFixed(0)}€
        </span>
        <span className="text-[10px] text-stone-400">{pct}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

interface CreateFormState {
  email: string; name: string; password: string; role: string; monthly_limit_usd: string
}

export function AdminPanel() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [editUser, setEditUser] = useState<AdminUser | null>(null)
  const [viewJobs, setViewJobs] = useState<{ user: AdminUser; jobs: UserJob[] } | null>(null)
  const [saving, setSaving] = useState(false)
  const [createForm, setCreateForm] = useState<CreateFormState>({
    email: '', name: '', password: '', role: 'user', monthly_limit_usd: '11',
  })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/admin/users`)
      if (res.ok) setUsers((await res.json()).users)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/admin/users`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...createForm,
          monthly_limit_usd: parseFloat(createForm.monthly_limit_usd) || 11,
        }),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        alert(d.detail || 'Erreur lors de la création.')
        return
      }
      setShowCreate(false)
      setCreateForm({ email: '', name: '', password: '', role: 'user', monthly_limit_usd: '11' })
      load()
    } finally {
      setSaving(false)
    }
  }

  async function handleToggleActive(user: AdminUser) {
    await apiFetch(`${BACKEND_URL}/api/admin/users/${user.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_active: !user.is_active }),
    })
    load()
  }

  async function handleSaveEdit() {
    if (!editUser) return
    setSaving(true)
    try {
      const res = await apiFetch(`${BACKEND_URL}/api/admin/users/${editUser.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: editUser.name,
          monthly_limit_usd: editUser.monthly_limit_usd,
          role: editUser.role,
        }),
      })
      if (!res.ok) { alert('Erreur lors de la mise à jour.'); return }
      setEditUser(null)
      load()
    } finally {
      setSaving(false)
    }
  }

  async function handleResetCredits(user: AdminUser) {
    if (!confirm(`Réinitialiser les crédits de ${user.email} ?`)) return
    await apiFetch(`${BACKEND_URL}/api/admin/users/${user.id}/reset-credits`, { method: 'POST' })
    load()
  }

  async function handleViewJobs(user: AdminUser) {
    const res = await apiFetch(`${BACKEND_URL}/api/admin/users/${user.id}/jobs`)
    if (res.ok) {
      const d = await res.json()
      setViewJobs({ user, jobs: d.jobs })
    }
  }

  if (loading) return (
    <div className="flex items-center justify-center py-24">
      <svg className="h-6 w-6 animate-spin text-indigo-300" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
    </div>
  )

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-stone-800">Administration</h2>
          <p className="text-xs text-stone-400 mt-0.5">{users.length} utilisateur{users.length > 1 ? 's' : ''}</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-1.5 rounded-xl border border-sage-200 bg-sage-50 px-3 py-1.5 text-xs font-medium text-sage-700 hover:bg-sage-100 transition-colors"
        >
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
          </svg>
          Nouvel utilisateur
        </button>
      </div>

      {/* Users table */}
      <div className="rounded-2xl bg-white border border-stone-200 shadow-sm overflow-hidden">
        <div className="divide-y divide-slate-100">
          {users.map((user) => (
            <div key={user.id} className={`px-5 py-4 ${!user.is_active ? 'opacity-50' : ''}`}>
              <div className="flex items-start gap-4">
                {/* Avatar */}
                <div className={`shrink-0 flex h-9 w-9 items-center justify-center rounded-full text-sm font-bold ${
                  user.role === 'admin' ? 'bg-indigo-100 text-indigo-700' : 'bg-stone-100 text-stone-600'
                }`}>
                  {(user.name || user.email)[0].toUpperCase()}
                </div>

                {/* Info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="text-sm font-medium text-stone-800">{user.name || '—'}</p>
                    <span className={`text-[10px] font-semibold rounded-full px-1.5 py-px ${
                      user.role === 'admin' ? 'bg-indigo-100 text-indigo-700' : 'bg-stone-100 text-stone-500'
                    }`}>{user.role}</span>
                    {!user.is_active && (
                      <span className="text-[10px] font-semibold rounded-full px-1.5 py-px bg-red-100 text-red-600">Suspendu</span>
                    )}
                  </div>
                  <p className="text-xs text-stone-400 mt-0.5">{user.email}</p>
                  <div className="flex items-center gap-3 mt-2 flex-wrap">
                    <CreditBar spent={user.spent_eur} limit={user.limit_eur} />
                    <span className="text-[11px] text-stone-400">{user.jobs_count} analyse{user.jobs_count > 1 ? 's' : ''}</span>
                    {user.last_login_at && (
                      <span className="text-[11px] text-stone-400">
                        Dernière connexion : {new Date(user.last_login_at).toLocaleDateString('fr-FR')}
                      </span>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    onClick={() => handleViewJobs(user)}
                    className="rounded-lg border border-slate-200 px-2 py-1 text-[11px] text-stone-600 hover:bg-slate-50 transition-colors"
                    title="Voir les analyses"
                  >
                    Analyses
                  </button>
                  <button
                    onClick={() => setEditUser({ ...user })}
                    className="rounded-lg border border-slate-200 px-2 py-1 text-[11px] text-stone-600 hover:bg-slate-50 transition-colors"
                    title="Modifier"
                  >
                    Modifier
                  </button>
                  <button
                    onClick={() => handleResetCredits(user)}
                    className="rounded-lg border border-slate-200 px-2 py-1 text-[11px] text-stone-600 hover:bg-slate-50 transition-colors"
                    title="Réinitialiser les crédits"
                  >
                    Reset crédits
                  </button>
                  <button
                    onClick={() => handleToggleActive(user)}
                    className={`rounded-lg border px-2 py-1 text-[11px] transition-colors ${
                      user.is_active
                        ? 'border-red-200 text-red-600 hover:bg-red-50'
                        : 'border-emerald-200 text-emerald-600 hover:bg-emerald-50'
                    }`}
                  >
                    {user.is_active ? 'Suspendre' : 'Réactiver'}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Create user modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-6">
            <h3 className="text-sm font-semibold text-stone-800 mb-4">Nouvel utilisateur</h3>
            <form onSubmit={handleCreate} className="space-y-3">
              {[
                { label: 'Email', key: 'email', type: 'email', placeholder: 'email@exemple.fr' },
                { label: 'Nom (optionnel)', key: 'name', type: 'text', placeholder: 'Prénom Nom' },
                { label: 'Mot de passe', key: 'password', type: 'password', placeholder: '••••••••' },
              ].map(({ label, key, type, placeholder }) => (
                <div key={key}>
                  <label className="block text-xs font-medium text-stone-600 mb-1">{label}</label>
                  <input
                    type={type}
                    value={(createForm as unknown as Record<string, string>)[key]}
                    onChange={e => setCreateForm(prev => ({ ...prev, [key]: e.target.value }))}
                    required={key !== 'name'}
                    placeholder={placeholder}
                    className="w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm focus:border-sage-400 focus:outline-none focus:ring-2 focus:ring-sage-200"
                  />
                </div>
              ))}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-stone-600 mb-1">Rôle</label>
                  <select
                    value={createForm.role}
                    onChange={e => setCreateForm(prev => ({ ...prev, role: e.target.value }))}
                    className="w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm focus:outline-none"
                  >
                    <option value="user">Utilisateur</option>
                    <option value="admin">Admin</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-stone-600 mb-1">Limite mensuelle (€)</label>
                  <input
                    type="number"
                    min="0"
                    step="1"
                    value={createForm.monthly_limit_usd}
                    onChange={e => setCreateForm(prev => ({ ...prev, monthly_limit_usd: e.target.value }))}
                    className="w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm focus:outline-none"
                  />
                  <p className="text-[10px] text-stone-400 mt-0.5">0 = illimité</p>
                </div>
              </div>
              <div className="flex gap-2 pt-2">
                <button type="button" onClick={() => setShowCreate(false)} className="flex-1 rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
                  Annuler
                </button>
                <button type="submit" disabled={saving} className="flex-1 rounded-xl bg-sage-600 px-4 py-2 text-sm font-semibold text-white hover:bg-sage-700 disabled:opacity-50">
                  {saving ? 'Création…' : 'Créer'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit user modal */}
      {editUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-6">
            <h3 className="text-sm font-semibold text-stone-800 mb-4">Modifier — {editUser.email}</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-stone-600 mb-1">Nom</label>
                <input
                  type="text"
                  value={editUser.name || ''}
                  onChange={e => setEditUser(prev => prev ? { ...prev, name: e.target.value } : null)}
                  className="w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sage-200"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-stone-600 mb-1">Rôle</label>
                  <select
                    value={editUser.role}
                    onChange={e => setEditUser(prev => prev ? { ...prev, role: e.target.value as 'user' | 'admin' } : null)}
                    className="w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm"
                  >
                    <option value="user">Utilisateur</option>
                    <option value="admin">Admin</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-stone-600 mb-1">Limite ($ USD)</label>
                  <input
                    type="number" min="0" step="0.5"
                    value={editUser.monthly_limit_usd}
                    onChange={e => setEditUser(prev => prev ? { ...prev, monthly_limit_usd: parseFloat(e.target.value) || 0 } : null)}
                    className="w-full rounded-xl border border-stone-200 bg-stone-50 px-3 py-2 text-sm"
                  />
                  <p className="text-[10px] text-stone-400 mt-0.5">0 = illimité</p>
                </div>
              </div>
            </div>
            <div className="flex gap-2 mt-4">
              <button onClick={() => setEditUser(null)} className="flex-1 rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">
                Annuler
              </button>
              <button onClick={handleSaveEdit} disabled={saving} className="flex-1 rounded-xl bg-sage-600 px-4 py-2 text-sm font-semibold text-white hover:bg-sage-700 disabled:opacity-50">
                {saving ? 'Enregistrement…' : 'Enregistrer'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* View user jobs modal */}
      {viewJobs && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-xl max-h-[80vh] flex flex-col overflow-hidden">
            <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
              <div>
                <p className="text-sm font-semibold text-stone-800">Analyses de {viewJobs.user.email}</p>
                <p className="text-xs text-stone-400 mt-0.5">{viewJobs.jobs.length} analyse{viewJobs.jobs.length > 1 ? 's' : ''}</p>
              </div>
              <button onClick={() => setViewJobs(null)} className="rounded-full p-1.5 text-stone-400 hover:text-stone-600 hover:bg-slate-100">
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="overflow-y-auto divide-y divide-slate-100">
              {viewJobs.jobs.length === 0 ? (
                <p className="text-center text-sm text-stone-400 py-10">Aucune analyse.</p>
              ) : viewJobs.jobs.map((job) => (
                <div key={job.id} className="flex items-center gap-3 px-5 py-3">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-stone-800 truncate">{job.filename}</p>
                    <p className="text-xs text-stone-400 mt-0.5">
                      {job.created_at ? new Date(job.created_at).toLocaleString('fr-FR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—'}
                      {' · '}{job.corrections_count} corrections
                      {job.false_positives_count > 0 && ` · ${job.false_positives_count} FP`}
                      {job.actual_cost_eur != null && ` · ${job.actual_cost_eur.toFixed(3)}€`}
                    </p>
                  </div>
                  <span className={`text-[11px] font-medium rounded-full px-2 py-0.5 ${
                    job.status === 'done' ? 'bg-green-100 text-green-700' :
                    job.status === 'error' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'
                  }`}>
                    {job.status === 'done' ? 'Terminé' : job.status === 'error' ? 'Erreur' : job.status}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
