import { Fragment, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, FolderOpen, HardDrive, Cloud, Server, CheckCircle2, AlertTriangle, RefreshCw, ChevronRight, ChevronDown, PieChart } from 'lucide-react'
import { api, type AppT, type BackupTarget } from '../api'
import { Badge, ConfirmModal, ErrorBox, Field, Loading, Modal, PageHeader, ProgressBar, Section, Spinner, Toggle } from '../components/ui'
import { useToast } from '../components/toast'
import { useAuth } from '../auth'
import { fmtBytes } from '../util'
import { ManagerBadge } from '../components/ManagerBadge'

const DIRS: { key: 'apps' | 'backups' | 'volumes' | 'data'; label: string; hint: string }[] = [
  { key: 'apps', label: 'Thư mục ứng dụng (compose + .env)', hint: 'Mỗi app một thư mục chuẩn: docker-compose.yml, .env, override, README — tự chạy được không cần panel.' },
  { key: 'backups', label: 'Thư mục backup cục bộ', hint: 'Nơi lưu bản backup trước khi (tuỳ chọn) đẩy lên đích ngoài.' },
  { key: 'volumes', label: 'Thư mục dữ liệu volume', hint: 'Đặt → app mới lưu named volume dạng thư mục <dir>/<slug>/<volume> (xem/chép trực tiếp). Trống = volume Docker thường.' },
  { key: 'data', label: 'Thư mục data mount', hint: 'Đặt → app mới có biến APP_DATA_DIR=<dir>/<slug> trong .env để bind mount (vd ${APP_DATA_DIR}/uploads:/app/uploads).' },
]

const KIND_ICON = { local: <HardDrive className="h-4 w-4" />, s3: <Cloud className="h-4 w-4" />, sftp: <Server className="h-4 w-4" /> }

function TargetForm({ t, onClose, onSaved }: { t: BackupTarget | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [name, setName] = useState(t?.name || '')
  const [kind, setKind] = useState<BackupTarget['kind']>(t?.kind || 's3')
  const [cfg, setCfg] = useState<Record<string, any>>(t?.config || { path_style: true, port: 22, auth: 'password' })
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null)
  const set = (k: string, v: any) => setCfg({ ...cfg, [k]: v })
  const body = () => ({ name, kind, config: cfg, secret: secret || null })
  const inp = (k: string, label: string, ph = '', type = 'text', hint?: string) => (
    <Field label={label} hint={hint}><input className="input font-mono" type={type} value={cfg[k] ?? ''} placeholder={ph} onChange={(e) => set(k, type === 'number' ? Number(e.target.value) : e.target.value)} /></Field>
  )
  async function doTest() {
    setBusy(true); setTest(null)
    try { setTest(await api.post(`/api/storage/targets/test${t ? `?target_id=${t.id}` : ''}`, body())) } catch (e: any) { setTest({ ok: false, message: e.message }) } finally { setBusy(false) }
  }
  async function save() {
    setBusy(true)
    try {
      t ? await api.put(`/api/storage/targets/${t.id}`, body()) : await api.post('/api/storage/targets', body())
      toast.success('Đã lưu đích backup'); onSaved(); onClose()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }
  const secretHint = t?.has_secret ? 'Để trống = giữ nguyên' : undefined
  return (
    <Modal open onClose={onClose} title={t ? `Sửa đích backup: ${t.name}` : 'Thêm đích backup'} size="lg" footer={<>
      <button className="btn-secondary" disabled={busy} onClick={doTest}>{busy && <Spinner />}Kiểm tra kết nối</button>
      <button className="btn-secondary" onClick={onClose}>Huỷ</button>
      <button className="btn-primary" disabled={busy || !name} onClick={save}>Lưu</button>
    </>}>
      <div className="space-y-3">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Tên"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Loại">
            <select className="input" value={kind} onChange={(e) => setKind(e.target.value as BackupTarget['kind'])}>
              <option value="s3">S3 (AWS, Cloudflare R2, Backblaze B2, Wasabi, MinIO…)</option>
              <option value="sftp">SFTP</option>
              <option value="local">Thư mục cục bộ (ổ gắn ngoài, NFS…)</option>
            </select>
          </Field>
        </div>
        {kind === 'local' && inp('path', 'Đường dẫn tuyệt đối', '/data/backup-offsite')}
        {kind === 's3' && (
          <div className="grid gap-3 md:grid-cols-2">
            {inp('endpoint', 'Endpoint', 'https://s3.amazonaws.com · https://<acc>.r2.cloudflarestorage.com · http://minio:9000', 'text', 'Trống = AWS S3')}
            {inp('bucket', 'Bucket', 'my-backups')}
            {inp('region', 'Region', 'us-east-1 / auto')}
            {inp('prefix', 'Prefix (thư mục trong bucket)', 'tekar')}
            {inp('access_key', 'Access key')}
            <Field label="Secret key" hint={secretHint}><input className="input font-mono" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} /></Field>
            <Toggle checked={cfg.path_style ?? true} onChange={(v) => set('path_style', v)} label="Path-style URL (MinIO, R2)" />
          </div>
        )}
        {kind === 'sftp' && (
          <div className="grid gap-3 md:grid-cols-2">
            {inp('host', 'Host', 'backup.example.com')}
            {inp('port', 'Cổng', '22', 'number')}
            {inp('username', 'Username')}
            {inp('path', 'Thư mục đích', '/backups/tekar', 'text', 'Tương đối = theo thư mục home')}
            <Field label="Xác thực">
              <select className="input" value={cfg.auth || 'password'} onChange={(e) => set('auth', e.target.value)}>
                <option value="password">Mật khẩu</option><option value="key">Private key</option>
              </select>
            </Field>
            {cfg.auth === 'key' ? (
              <>
                <Field label="Private key (PEM / OpenSSH)" hint={secretHint} className="md:col-span-2"><textarea className="input font-mono text-xs" rows={5} value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" /></Field>
                {inp('key_passphrase', 'Passphrase (nếu có)', '', 'password')}
              </>
            ) : (
              <Field label="Mật khẩu" hint={secretHint}><input className="input" type="password" value={secret} onChange={(e) => setSecret(e.target.value)} /></Field>
            )}
          </div>
        )}
        {test && (
          <div className={`flex items-start gap-1 text-sm ${test.ok ? 'text-emerald-600' : 'text-rose-600'}`}>
            {test.ok ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />}{test.message}
          </div>
        )}
      </div>
    </Modal>
  )
}

function TargetFiles({ t, onClose }: { t: BackupTarget; onClose: () => void }) {
  const toast = useToast()
  const q = useQuery({ queryKey: ['target-files', t.id], queryFn: () => api.get<any[]>(`/api/storage/targets/${t.id}/files`) })
  const apps = useQuery({ queryKey: ['apps'], queryFn: () => api.get<AppT[]>('/api/apps') })
  const [imp, setImp] = useState<string | null>(null)
  const [appId, setAppId] = useState<number | ''>('')
  const [done, setDone] = useState<number | null>(null)
  return (
    <Modal open onClose={onClose} title={`File trên đích: ${t.name}`} size="lg">
      {q.isLoading && <Loading />}
      <ErrorBox error={q.error} />
      {done != null && <div className="mb-3 text-sm text-emerald-600">Đã nhập. <Link className="underline" to={`/apps/${done}/backup`}>Mở tab Backup của app để khôi phục</Link></div>}
      <table className="table">
        <thead><tr><th>File</th><th>Kích thước</th><th>Thời gian</th><th></th></tr></thead>
        <tbody>
          {(q.data || []).map((f) => (
            <tr key={f.key}>
              <td className="mono break-all">{f.key}</td>
              <td className="whitespace-nowrap">{fmtBytes(f.size)}</td>
              <td className="whitespace-nowrap text-xs">{f.modified ? new Date(f.modified * 1000).toLocaleString('vi-VN', { hour12: false }) : '—'}</td>
              <td className="whitespace-nowrap text-right">{f.in_panel ? <Badge color="green">đã có trong panel</Badge> : <button className="btn-secondary btn-sm" onClick={() => {
                setImp(f.key)
                const slug = f.key.split('/')[0]
                setAppId(apps.data?.find((a) => a.slug === slug)?.id ?? '')
              }}>Nhập để khôi phục</button>}</td>
            </tr>
          ))}
          {q.data && !q.data.length && <tr><td colSpan={4} className="text-center text-slate-500">Chưa có file backup</td></tr>}
        </tbody>
      </table>
      {imp && (
        <div className="mt-3 flex flex-wrap items-end gap-2 rounded-md border border-slate-200 p-3 dark:border-slate-800">
          <Field label={`Nhập ${imp} cho ứng dụng`}>
            <select className="input w-64" value={appId} onChange={(e) => setAppId(e.target.value ? Number(e.target.value) : '')}>
              <option value="">— Chọn app —</option>
              {(apps.data || []).filter((a) => a.my_role === 'owner').map((a) => <option key={a.id} value={a.id}>{a.name} ({a.slug})</option>)}
            </select>
          </Field>
          <button className="btn-primary" disabled={!appId} onClick={async () => {
            try { await api.post(`/api/storage/targets/${t.id}/import`, { key: imp, app_id: appId }); toast.success('Đã nhập bản backup'); setDone(Number(appId)); setImp(null); q.refetch() } catch (e) { toast.error(e) }
          }}>Nhập</button>
          <button className="btn-ghost" onClick={() => setImp(null)}>Huỷ</button>
        </div>
      )}
    </Modal>
  )
}

const USAGE_COLS: { key: string; label: string; color: string }[] = [
  { key: 'folder', label: 'Thư mục', color: 'bg-sky-500' },
  { key: 'binds', label: 'Bind mount', color: 'bg-violet-500' },
  { key: 'volumes', label: 'Volume', color: 'bg-emerald-500' },
  { key: 'containers', label: 'Lớp container', color: 'bg-amber-500' },
  { key: 'backups', label: 'Backup', color: 'bg-rose-500' },
]
const KIND_LABEL: Record<string, string> = { folder: 'Thư mục', binds: 'Bind', volumes: 'Volume', containers: 'Container', images: 'Image', backups: 'Backup' }

function StackUsage() {
  const toast = useToast()
  const q = useQuery({
    queryKey: ['stack-usage'], queryFn: () => api.get<any>('/api/storage/usage'),
    refetchInterval: (query) => (query.state.data?.job_id ? 3000 : false),
  })
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [search, setSearch] = useState('')
  const [hideEmpty, setHideEmpty] = useState(true)
  const d = q.data
  const running = !!d?.job_id
  useEffect(() => { if (d && !d.updated_at && !d.job_id) refresh(true) }, [d?.updated_at, d?.job_id])  // eslint-disable-line react-hooks/exhaustive-deps
  async function refresh(silent = false) {
    try { await api.post('/api/storage/usage/refresh'); q.refetch() } catch (e) { if (!silent) toast.error(e) }
  }
  const stacks: any[] = (d?.stacks || []).filter((s: any) => (!hideEmpty || s.total > 0) && (!search || s.project.toLowerCase().includes(search.toLowerCase())))
  const max = Math.max(1, ...stacks.map((s) => s.total))
  const sum = stacks.reduce((a, s) => a + s.total, 0)
  return (
    <Section title={<span className="flex items-center gap-1"><PieChart className="h-4 w-4" />Dung lượng theo stack</span>} actions={<>
      <input className="input w-44 py-1 text-sm" placeholder="Tìm stack…" value={search} onChange={(e) => setSearch(e.target.value)} />
      <Toggle checked={hideEmpty} onChange={setHideEmpty} label="Ẩn stack 0 B" />
      <button className="btn-secondary btn-sm" disabled={running} onClick={() => refresh()}>{running ? <Spinner /> : <RefreshCw className="h-3.5 w-3.5" />}{running ? 'Đang tính…' : 'Tính lại'}</button>
    </>}>
      {q.isLoading && <Loading />}
      <ErrorBox error={q.error} />
      {d && !d.updated_at && <div className="p-4 text-sm text-slate-500">{running ? 'Đang tính dung lượng lần đầu…' : 'Chưa có số liệu.'}</div>}
      {d?.updated_at && (
        <div className="space-y-3 p-4">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-500">
            {(d.disks || []).map((k: any) => (
              <span key={k.path} className="flex items-center gap-2">
                Ổ <span className="font-mono">{k.path}</span>
                <ProgressBar value={k.used} max={k.total} className="w-24" />
                {fmtBytes(k.used)} / {fmtBytes(k.total)}
              </span>
            ))}
            <span>Các stack đang hiện: <b>{fmtBytes(sum)}</b></span>
            <span>Cập nhật {new Date(d.updated_at).toLocaleString('vi-VN', { hour12: false })} ({d.duration}s) · tự tính lại mỗi 6 giờ</span>
          </div>
          <div className="flex flex-wrap gap-3 text-xs text-slate-500">
            {USAGE_COLS.map((c) => <span key={c.key} className="flex items-center gap-1"><span className={`h-2 w-2 rounded-sm ${c.color}`} />{c.label}</span>)}
            <span>· Image dùng chung giữa các stack nên không cộng vào tổng.</span>
          </div>
          <div className="overflow-x-auto">
            <table className="table">
              <thead><tr>
                <th>Stack</th>
                {USAGE_COLS.map((c) => <th key={c.key} className="text-right">{c.label}</th>)}
                <th className="text-right">Tổng</th><th className="w-40"></th><th className="text-right text-slate-400">Image</th>
              </tr></thead>
              <tbody>
                {stacks.map((s) => {
                  const k = (s.standalone ? 'c:' : '') + s.project
                  return (
                    <Fragment key={k}>
                      <tr className="cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-900" onClick={() => setOpen({ ...open, [k]: !open[k] })}>
                        <td>
                          <div className="flex items-center gap-1.5">
                            {open[k] ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
                            <span className="font-medium">{s.project}</span>
                            <ManagerBadge m={s.manager} />
                            {s.app && <Link className="text-xs text-sky-600 hover:underline" to={`/apps/${s.app.id}`} onClick={(e) => e.stopPropagation()}>app</Link>}
                          </div>
                          <div className="ml-5 text-xs text-slate-500">{s.running}/{s.containers} container chạy{s.work_dir && <> · <span className="font-mono">{s.work_dir}</span></>}</div>
                        </td>
                        {USAGE_COLS.map((c) => <td key={c.key} className="whitespace-nowrap text-right tabular-nums">{s.sizes[c.key] ? fmtBytes(s.sizes[c.key]) : <span className="text-slate-400">—</span>}</td>)}
                        <td className="whitespace-nowrap text-right font-semibold tabular-nums">{fmtBytes(s.total)}</td>
                        <td>
                          <div className="flex h-2 overflow-hidden rounded bg-slate-100 dark:bg-slate-800" style={{ width: `${Math.max(2, (s.total / max) * 100)}%` }}>
                            {USAGE_COLS.map((c) => s.sizes[c.key] ? <div key={c.key} className={c.color} style={{ width: `${(s.sizes[c.key] / (s.total || 1)) * 100}%` }} /> : null)}
                          </div>
                        </td>
                        <td className="whitespace-nowrap text-right text-xs tabular-nums text-slate-400">{fmtBytes(s.sizes.images)}</td>
                      </tr>
                      {open[k] && (
                        <tr><td colSpan={USAGE_COLS.length + 4} className="bg-slate-50 dark:bg-slate-900/50">
                          <table className="w-full text-xs">
                            <tbody>
                              {s.items.map((it: any, i: number) => (
                                <tr key={i} className="border-b border-slate-200/60 last:border-0 dark:border-slate-800/60">
                                  <td className="w-24 py-1"><Badge>{KIND_LABEL[it.kind] || it.kind}</Badge></td>
                                  <td className="py-1 font-mono break-all">{it.name}{it.path && it.path !== it.name && <span className="text-slate-500"> → {it.path}</span>}</td>
                                  <td className="py-1 text-slate-500">
                                    {it.note && <span className="text-amber-600">bỏ qua: {it.note}</span>}
                                    {it.detail && <span>{it.detail}</span>}
                                    {it.shared && <Badge color="blue" title="Cũng được stack khác dùng">dùng chung</Badge>}
                                  </td>
                                  <td className="whitespace-nowrap py-1 text-right tabular-nums">{it.size != null ? fmtBytes(it.size) : '—'}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td></tr>
                      )}
                    </Fragment>
                  )
                })}
                {!stacks.length && <tr><td colSpan={USAGE_COLS.length + 4} className="text-center text-slate-500">Không có stack</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Section>
  )
}

export default function Storage() {
  const toast = useToast()
  const { user } = useAuth()
  const admin = user?.role === 'admin'
  const st = useQuery({ queryKey: ['storage'], queryFn: () => api.get('/api/storage') })
  const targets = useQuery({ queryKey: ['backup-targets'], queryFn: () => api.get<BackupTarget[]>('/api/storage/targets') })
  const [dirs, setDirs] = useState<Record<string, string>>({})
  const [defTarget, setDefTarget] = useState<number | ''>('')
  const [busy, setBusy] = useState(false)
  const [edit, setEdit] = useState<BackupTarget | null | undefined>(undefined)
  const [del, setDel] = useState<BackupTarget | null>(null)
  const [files, setFiles] = useState<BackupTarget | null>(null)
  useEffect(() => {
    if (st.data) {
      setDirs(Object.fromEntries(DIRS.map((d) => [d.key, st.data.dirs[d.key]?.custom ? st.data.dirs[d.key].path : ''])))
      setDefTarget(st.data.default_backup_destination_id ?? '')
    }
  }, [st.data])

  async function save() {
    setBusy(true)
    try {
      const r = await api.put('/api/storage', { ...dirs, default_backup_destination_id: defTarget || null })
      toast.success(r.note || 'Đã lưu'); st.refetch()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Lưu trữ & Backup" subtitle="Thư mục lưu trữ vendor-free và nơi lưu backup ngoài máy (S3 / SFTP / thư mục)" />
      <Section title={<span className="flex items-center gap-1"><FolderOpen className="h-4 w-4" />Thư mục lưu trữ</span>}>
        {st.isLoading && <Loading />}
        <ErrorBox error={st.error} />
        {st.data && (
          <div className="space-y-4 p-4">
            <div className="text-xs text-slate-500">Panel truy cập được: <span className="font-mono">{st.data.roots.join(', ')}</span> — thư mục phải nằm dưới một trong các gốc này (đổi TH_STORAGE_ROOT trong .env nếu cần).</div>
            <div className="grid gap-4 md:grid-cols-2">
              {DIRS.map((d) => {
                const info = st.data.dirs[d.key] || {}
                return (
                  <Field key={d.key} label={d.label} hint={d.hint}>
                    <input className="input font-mono" disabled={!admin} value={dirs[d.key] ?? ''} placeholder={info.custom ? '' : info.path || '(không dùng)'} onChange={(e) => setDirs({ ...dirs, [d.key]: e.target.value })} />
                    {info.total ? (
                      <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                        <span className="font-mono">{info.path}</span>
                        <ProgressBar value={info.total - info.free} max={info.total} className="w-24" />
                        <span>trống {fmtBytes(info.free)}</span>
                      </div>
                    ) : null}
                  </Field>
                )
              })}
              <Field label="Đích backup mặc định cho app mới">
                <select className="input" disabled={!admin} value={defTarget} onChange={(e) => setDefTarget(e.target.value ? Number(e.target.value) : '')}>
                  <option value="">Chỉ lưu cục bộ</option>
                  {(targets.data || []).map((t) => <option key={t.id} value={t.id}>{t.name} ({t.kind})</option>)}
                </select>
              </Field>
            </div>
            <div className="text-xs text-slate-500">Thay đổi áp dụng cho app tạo mới và backup mới; app hiện có giữ thư mục của nó.</div>
            {admin && <button className="btn-primary" disabled={busy} onClick={save}>{busy && <Spinner />}Lưu</button>}
          </div>
        )}
      </Section>
      <StackUsage />
      <Section title="Đích backup" actions={<button className="btn-primary btn-sm" onClick={() => setEdit(null)}><Plus className="h-3.5 w-3.5" />Thêm đích</button>}>
        {targets.isLoading && <Loading />}
        <ErrorBox error={targets.error} />
        <table className="table">
          <thead><tr><th>Tên</th><th>Loại</th><th>Địa chỉ</th><th>App dùng</th><th></th></tr></thead>
          <tbody>
            {(targets.data || []).map((t) => (
              <tr key={t.id}>
                <td className="font-medium"><span className="inline-flex items-center gap-1">{KIND_ICON[t.kind]}{t.name}</span></td>
                <td><Badge color={t.kind === 's3' ? 'blue' : t.kind === 'sftp' ? 'purple' : 'gray'}>{t.kind}</Badge></td>
                <td className="mono break-all">
                  {t.kind === 's3' && `${t.config.endpoint || 'AWS S3'} / ${t.config.bucket}${t.config.prefix ? '/' + t.config.prefix : ''}`}
                  {t.kind === 'sftp' && `${t.config.username}@${t.config.host}:${t.config.port || 22}${t.config.path ? ' ' + t.config.path : ''}`}
                  {t.kind === 'local' && t.config.path}
                </td>
                <td>{t.apps ?? 0}</td>
                <td className="whitespace-nowrap text-right">
                  <button className="btn-ghost btn-sm" onClick={() => setFiles(t)}>Xem file</button>
                  <button className="btn-ghost btn-sm" onClick={() => setEdit(t)}><Pencil className="h-3.5 w-3.5" /></button>
                  <button className="btn-ghost btn-sm" onClick={() => setDel(t)}><Trash2 className="h-3.5 w-3.5" /></button>
                </td>
              </tr>
            ))}
            {targets.data && !targets.data.length && <tr><td colSpan={5} className="text-center text-slate-500">Chưa có đích backup — backup chỉ lưu cục bộ</td></tr>}
          </tbody>
        </table>
      </Section>
      {edit !== undefined && <TargetForm t={edit} onClose={() => setEdit(undefined)} onSaved={() => targets.refetch()} />}
      {files && <TargetFiles t={files} onClose={() => setFiles(null)} />}
      <ConfirmModal open={!!del} onClose={() => setDel(null)} title="Xoá đích backup" message={<>Xoá <b>{del?.name}</b>? File đã đẩy lên không bị xoá; app đang dùng sẽ chuyển về chỉ lưu cục bộ.</>}
        onConfirm={async () => { try { await api.del(`/api/storage/targets/${del!.id}`); toast.success('Đã xoá'); setDel(null); targets.refetch() } catch (e) { toast.error(e) } }} />
    </div>
  )
}
