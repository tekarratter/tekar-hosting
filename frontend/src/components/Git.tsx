import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Github, Plus, Trash2, PlayCircle, GitBranch } from 'lucide-react'
import { api } from '../api'
import { Badge, Field, Spinner, Toggle } from './ui'
import { useToast } from './toast'
import { useIsStaff } from '../auth'
import { fmtAgo } from '../util'

export interface GitCred { id: number; name: string; provider: string; host: string; username: string; shared: boolean; mine: boolean }
export interface GitSel { repo: string; branch: string; credential_id: number | null; subdir: string }

export const useGitCreds = () => useQuery({ queryKey: ['git-creds'], queryFn: () => api.get<GitCred[]>('/api/git/credentials') })

const PROVIDERS = [
  { id: 'github', label: 'GitHub' },
  { id: 'gitlab', label: 'GitLab' },
  { id: 'generic', label: 'Khác (Gitea, Bitbucket…)' },
]

export function GitConnectForm({ onDone, onCancel }: { onDone: (c: GitCred) => void; onCancel?: () => void }) {
  const toast = useToast()
  const staff = useIsStaff()
  const [f, setF] = useState({ provider: 'github', host: '', username: '', token: '', name: '', shared: false })
  const [busy, setBusy] = useState(false)
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  return (
    <div className="space-y-2 rounded-md border border-indigo-200 bg-indigo-50/40 p-3 text-sm dark:border-indigo-900 dark:bg-indigo-950/20">
      <div className="grid gap-2 md:grid-cols-3">
        <Field label="Nhà cung cấp">
          <select className="input" value={f.provider} onChange={(e) => set('provider', e.target.value)}>{PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select>
        </Field>
        {f.provider !== 'github' && <Field label="Host" hint={f.provider === 'gitlab' ? 'Trống = gitlab.com' : 'vd git.congty.vn'}><input className="input font-mono" value={f.host} onChange={(e) => set('host', e.target.value)} /></Field>}
        {f.provider === 'generic' && <Field label="Tài khoản"><input className="input" value={f.username} onChange={(e) => set('username', e.target.value)} /></Field>}
        <Field label="Tên kết nối" hint="Trống = tự đặt theo tài khoản"><input className="input" value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
      </div>
      <Field label="Access token" hint={f.provider === 'github'
        ? <>Tạo <a className="underline" target="_blank" rel="noreferrer" href="https://github.com/settings/personal-access-tokens/new">fine-grained token</a>: chọn repo, quyền <b>Contents: Read-only</b> (+ Metadata). Hoặc token classic quyền <span className="font-mono">repo</span>.</>
        : f.provider === 'gitlab' ? 'Personal/Project access token, quyền read_repository + read_api.' : 'Token / app password có quyền đọc repo.'}>
        <input className="input font-mono" type="password" value={f.token} onChange={(e) => set('token', e.target.value)} placeholder={f.provider === 'github' ? 'github_pat_… / ghp_…' : ''} />
      </Field>
      {staff && <Toggle checked={f.shared} onChange={(v) => set('shared', v)} label="Chia sẻ cho mọi người dùng tạo app (token không bị lộ, chỉ dùng để clone)" />}
      <div className="flex gap-2">
        <button className="btn-primary btn-sm" disabled={!f.token || busy} onClick={async () => {
          setBusy(true)
          try {
            const c = await api.post<GitCred & { scopes?: string }>('/api/git/credentials', { ...f, name: f.name || '' })
            toast.success(`Đã kết nối ${c.username || c.host}`); onDone(c)
          } catch (e) { toast.error(e) } finally { setBusy(false) }
        }}>{busy && <Spinner />}Kết nối</button>
        {onCancel && <button className="btn-secondary btn-sm" onClick={onCancel}>Huỷ</button>}
      </div>
      <div className="text-xs text-slate-500">Token được mã hoá trong DB, chỉ gửi tới đúng host khi clone/kéo code, không ghi vào thư mục repo.</div>
    </div>
  )
}

export function GitConnections() {
  const toast = useToast()
  const q = useGitCreds()
  const [add, setAdd] = useState(false)
  const [test, setTest] = useState<Record<number, any>>({})
  return (
    <div className="space-y-3 p-4 text-sm">
      <p className="text-slate-600 dark:text-slate-400">Kết nối tài khoản GitHub / GitLab / Gitea để tạo app từ repo private và kéo code mới.</p>
      <table className="table">
        <thead><tr><th>Tên</th><th>Host</th><th>Tài khoản</th><th></th><th></th></tr></thead>
        <tbody>
          {(q.data || []).map((c) => (
            <tr key={c.id}>
              <td>{c.name} {c.shared && <Badge color="blue">chia sẻ</Badge>}</td>
              <td className="mono">{c.host}</td>
              <td>{c.username || '—'}</td>
              <td className="text-xs">{test[c.id] && (test[c.id].ok ? <span className="text-emerald-600">OK · {test[c.id].login}</span> : <span className="text-rose-600">{test[c.id].message}</span>)}</td>
              <td className="whitespace-nowrap text-right">
                <button className="btn-ghost btn-sm" title="Kiểm tra" onClick={async () => setTest({ ...test, [c.id]: await api.post(`/api/git/credentials/${c.id}/test`) })}><PlayCircle className="h-3.5 w-3.5" /></button>
                {c.mine && <button className="btn-ghost btn-sm" title="Xoá" onClick={async () => {
                  if (!confirm(`Xoá kết nối ${c.name}? App đang dùng sẽ không kéo được code private.`)) return
                  try { const r = await api.del(`/api/git/credentials/${c.id}`); toast.success(r.apps?.length ? `Đã xoá · app đang dùng: ${r.apps.join(', ')}` : 'Đã xoá'); q.refetch() } catch (e) { toast.error(e) }
                }}><Trash2 className="h-3.5 w-3.5" /></button>}
              </td>
            </tr>
          ))}
          {q.data && !q.data.length && <tr><td colSpan={5} className="text-center text-slate-500">Chưa có kết nối</td></tr>}
        </tbody>
      </table>
      {add ? <GitConnectForm onDone={() => { setAdd(false); q.refetch() }} onCancel={() => setAdd(false)} />
        : <button className="btn-secondary btn-sm" onClick={() => setAdd(true)}><Plus className="h-3.5 w-3.5" />Thêm kết nối</button>}
    </div>
  )
}

/** Chọn repo + nhánh + thư mục con; với kết nối GitHub/GitLab liệt kê repo của tài khoản. */
export function GitPicker({ value, onChange }: { value: GitSel; onChange: (v: GitSel) => void }) {
  const creds = useGitCreds()
  const [adding, setAdding] = useState(false)
  const [filter, setFilter] = useState('')
  const cred = (creds.data || []).find((c) => c.id === value.credential_id)
  const listable = cred && cred.provider !== 'generic'
  const repos = useQuery({ queryKey: ['git-repos', cred?.id], queryFn: () => api.get<any[]>(`/api/git/credentials/${cred!.id}/repos`), enabled: !!listable, staleTime: 60000 })
  const [repoInput, setRepoInput] = useState(value.repo)
  useEffect(() => setRepoInput(value.repo), [value.repo])
  const validRepo = /^https?:\/\/[^/]+\/[^/]+\/[^/]+|^[\w.-]+\/[\w.-]+$/.test(value.repo)
  const br = useQuery({
    queryKey: ['git-branches', value.repo, value.credential_id],
    queryFn: () => api.get<{ default: string; branches: string[] }>(`/api/git/branches?repo=${encodeURIComponent(value.repo)}${value.credential_id ? `&credential_id=${value.credential_id}` : ''}`),
    enabled: validRepo, retry: false, staleTime: 60000,
  })
  useEffect(() => { if (br.data && !value.branch && br.data.default) onChange({ ...value, branch: br.data.default }) }, [br.data]) // eslint-disable-line
  const shown = useMemo(() => (repos.data || []).filter((r) => !filter || r.full_name.toLowerCase().includes(filter.toLowerCase())).slice(0, 80), [repos.data, filter])

  return (
    <div className="space-y-3">
      <Field label="Kết nối git">
        <div className="flex gap-2">
          <select className="input" value={value.credential_id ?? ''} onChange={(e) => onChange({ ...value, credential_id: e.target.value ? Number(e.target.value) : null, branch: '' })}>
            <option value="">Repo public (không cần kết nối)</option>
            {(creds.data || []).map((c) => <option key={c.id} value={c.id}>{c.name} · {c.host}{c.username ? ` (${c.username})` : ''}</option>)}
          </select>
          <button className="btn-secondary whitespace-nowrap" onClick={() => setAdding(!adding)}><Github className="h-4 w-4" />Kết nối mới</button>
        </div>
      </Field>
      {adding && <GitConnectForm onDone={(c) => { setAdding(false); creds.refetch().then(() => onChange({ ...value, credential_id: c.id, branch: '' })) }} onCancel={() => setAdding(false)} />}
      {listable && (
        <div className="rounded-md border border-slate-200 dark:border-slate-800">
          <input className="w-full border-b border-slate-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-slate-800" placeholder={`Tìm repo của ${cred!.username || cred!.name}…`} value={filter} onChange={(e) => setFilter(e.target.value)} />
          <div className="max-h-48 overflow-y-auto">
            {repos.isLoading && <div className="p-2 text-xs text-slate-500"><Spinner /> Đang tải repo…</div>}
            {repos.error && <div className="p-2 text-xs text-rose-600">{(repos.error as Error).message}</div>}
            {shown.map((r) => (
              <button key={r.full_name} className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800 ${value.repo === r.url ? 'bg-indigo-50 dark:bg-indigo-950/40' : ''}`}
                onClick={() => onChange({ ...value, repo: r.url, branch: r.default_branch || '' })}>
                <span className="font-mono">{r.full_name}</span>
                {r.private && <Badge color="yellow">private</Badge>}
                <span className="ml-auto text-xs text-slate-500">{fmtAgo(r.updated_at)}</span>
              </button>
            ))}
            {repos.data && !shown.length && <div className="p-2 text-xs text-slate-500">Không có repo phù hợp</div>}
          </div>
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Repo" className="md:col-span-2" hint="https://github.com/owner/repo hoặc owner/repo">
          <input className="input font-mono" value={repoInput} onChange={(e) => setRepoInput(e.target.value)} onBlur={() => repoInput !== value.repo && onChange({ ...value, repo: repoInput.trim(), branch: '' })} placeholder="owner/repo" />
        </Field>
        <Field label={<span className="flex items-center gap-1"><GitBranch className="h-3.5 w-3.5" />Nhánh {br.isFetching && <Spinner />}</span>}>
          {br.data?.branches.length ? (
            <select className="input font-mono" value={value.branch} onChange={(e) => onChange({ ...value, branch: e.target.value })}>
              {br.data.branches.map((b) => <option key={b} value={b}>{b}{b === br.data!.default ? ' (mặc định)' : ''}</option>)}
            </select>
          ) : <input className="input font-mono" value={value.branch} onChange={(e) => onChange({ ...value, branch: e.target.value })} placeholder="main" />}
        </Field>
      </div>
      {br.error && <div className="text-xs text-rose-600">{(br.error as Error).message}</div>}
      <Field label="Thư mục con (tuỳ chọn)" hint="Monorepo: thư mục chứa Dockerfile / compose của app"><input className="input font-mono" value={value.subdir} onChange={(e) => onChange({ ...value, subdir: e.target.value })} placeholder="apps/web" /></Field>
    </div>
  )
}
