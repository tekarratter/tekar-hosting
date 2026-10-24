import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye, ArrowRightLeft, Hand, RefreshCw, AlertTriangle } from 'lucide-react'
import { api, type StackInfo } from '../api'
import { Badge, Code, Drawer, ErrorBox, Field, Loading, Modal, PageHeader, Spinner, Toggle } from '../components/ui'
import JobModal from '../components/JobModal'
import { useToast } from '../components/toast'
import { useAuth } from '../auth'
import { ManagerBadge } from '../components/ManagerBadge'

function slugify(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'app'
}

function StackDrawer({ project, onClose }: { project: string; onClose: () => void }) {
  const { user } = useAuth()
  const [reveal, setReveal] = useState(false)
  const q = useQuery({ queryKey: ['stack', project, reveal], queryFn: () => api.get(`/api/stacks/${encodeURIComponent(project)}${reveal ? '?reveal=true' : ''}`) })
  const d = q.data
  return (
    <Drawer open onClose={onClose} title={<span className="flex items-center gap-2">{project} {d && <ManagerBadge m={d.manager} />}</span>}>
      {q.isLoading && <Loading />}
      <ErrorBox error={q.error} />
      {d && (
        <div className="space-y-4 text-sm">
          {d.notes?.length > 0 && (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
              <ul className="list-disc pl-5">{d.notes.map((n: string, i: number) => <li key={i}>{n}</li>)}</ul>
            </div>
          )}
          <div className="grid gap-1 text-xs">
            <div>Thư mục (theo label): <span className="font-mono">{d.work_dir || '—'}</span></div>
            {d.host_work_dir && d.host_work_dir !== d.work_dir && <div>Trên host: <span className="font-mono">{d.host_work_dir}</span></div>}
            <div>Container: {d.containers.map((c: any) => <Badge key={c.name} color={c.state === 'running' ? 'green' : 'gray'}>{c.name}</Badge>)}</div>
          </div>
          <div>
            <div className="mb-1 font-medium">Compose <span className="font-mono text-xs text-slate-500">{d.compose_path || '(không đọc được)'}</span></div>
            {d.compose ? <Code>{d.compose}</Code> : <div className="text-slate-500">Không đọc được file compose.</div>}
          </div>
          <div>
            <div className="mb-1 flex items-center justify-between">
              <div className="font-medium">Biến môi trường <span className="font-mono text-xs text-slate-500">{d.env_path || '(khôi phục từ container)'}</span></div>
              {user?.role === 'admin' && <Toggle checked={reveal} onChange={setReveal} label="Hiện giá trị" />}
            </div>
            {d.env ? <Code>{d.env}</Code> : <div className="text-slate-500">Không có</div>}
          </div>
        </div>
      )}
    </Drawer>
  )
}

function ConvertModal({ stack, onClose, onJob }: { stack: StackInfo; onClose: () => void; onJob: (id: number) => void }) {
  const toast = useToast()
  const [slug, setSlug] = useState(stack.panel?.slug || slugify(stack.project))
  const [name, setName] = useState(stack.project)
  const [copyDir, setCopyDir] = useState(false)
  const [start, setStart] = useState(true)
  const [busy, setBusy] = useState(false)
  const external = ['dockhand', 'portainer', 'dockge', 'other'].includes(stack.manager.key)
  return (
    <Modal open onClose={onClose} title={`Chuyển "${stack.project}" về ứng dụng của panel`} size="md" footer={<>
      <button className="btn-secondary" onClick={onClose}>Huỷ</button>
      <button className="btn-primary" disabled={busy || !slug} onClick={async () => {
        setBusy(true)
        try { const r = await api.post(`/api/stacks/${encodeURIComponent(stack.project)}/convert`, { slug, name, copy_dir: copyDir, start }); onClose(); onJob(r.job_id) } catch (e) { toast.error(e) } finally { setBusy(false) }
      }}>{busy && <Spinner />}<ArrowRightLeft className="h-4 w-4" />Chuyển đổi</button>
    </>}>
      <div className="space-y-3 text-sm">
        <div className="text-slate-600 dark:text-slate-300">
          Panel chép compose + .env sang thư mục ứng dụng (vendor-free, tự chạy được bằng <span className="font-mono">docker compose up -d</span>),
          giữ nguyên tên project <b className="font-mono">{stack.project}</b> nên volume và network không đổi — dữ liệu không mất.
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Slug"><input className="input font-mono" value={slug} onChange={(e) => setSlug(e.target.value)} /></Field>
          <Field label="Tên hiển thị"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        </div>
        <div className="space-y-1">
          <Toggle checked={copyDir} onChange={setCopyDir} label="Chép cả thư mục nguồn (code, file cấu hình, dữ liệu bind tương đối)" />
          <div className="pl-11 text-xs text-slate-500">
            {copyDir ? 'Container sẽ dừng trong lúc chép (bỏ qua .git, node_modules). Có thể lâu nếu dữ liệu lớn.' : 'Mặc định: đường dẫn tương đối được đổi thành tuyệt đối, dữ liệu bind vẫn nằm tại thư mục cũ.'}
          </div>
          <Toggle checked={start} onChange={setStart} label="Tạo lại container từ thư mục mới ngay (gián đoạn vài giây)" />
        </div>
        {external && (
          <div className="flex gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <div>Sau khi chuyển: xoá stack <b>{stack.project}</b> khỏi {stack.manager.label} <b>KHÔNG chọn xoá volume</b>, để {stack.manager.label} không deploy đè lại (đặc biệt stack git có auto-update).</div>
          </div>
        )}
      </div>
    </Modal>
  )
}

export default function Stacks() {
  const toast = useToast()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['stacks'], queryFn: () => api.get<StackInfo[]>('/api/stacks') })
  const [view, setView] = useState<string | null>(null)
  const [convert, setConvert] = useState<StackInfo | null>(null)
  const [job, setJob] = useState<number | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [search, setSearch] = useState('')
  const data = q.data || []
  const managers = [...new Map(data.map((s) => [s.manager.key, s.manager])).values()]
  const list = data.filter((s) => (!filter || s.manager.key === filter) && (!search || s.project.toLowerCase().includes(search.toLowerCase())))

  async function adopt(project: string) {
    setBusy(project)
    try {
      await api.post('/api/apps/adopt', { project })
      toast.success(`${project}: đã quản lý tại chỗ`)
      q.refetch(); qc.invalidateQueries({ queryKey: ['apps'] })
    } catch (e) { toast.error(e) } finally { setBusy(null) }
  }

  return (
    <div>
      <PageHeader title="Stack trên host" subtitle="Mọi dự án docker compose đang chạy — ai đang quản lý, xem compose/env, chuyển về panel hoặc giữ nguyên"
        actions={<button className="btn-secondary" onClick={() => q.refetch()}>{q.isFetching ? <Spinner /> : <RefreshCw className="h-4 w-4" />}Quét lại</button>} />
      <div className="card">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 p-3 dark:border-slate-800">
          <button className={!filter ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'} onClick={() => setFilter('')}>Tất cả ({data.length})</button>
          {managers.map((m) => (
            <button key={m.key} className={filter === m.key ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'} onClick={() => setFilter(m.key)}>
              {m.label} ({data.filter((s) => s.manager.key === m.key).length})
            </button>
          ))}
          <input className="input ml-auto w-56" placeholder="Tìm project…" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        {q.isLoading && <Loading />}
        <ErrorBox error={q.error} />
        <div className="overflow-x-auto">
          <table className="table">
            <thead><tr><th>Project</th><th>Quản lý bởi</th><th>Panel</th><th>Container</th><th>Image</th><th>Thư mục</th><th></th></tr></thead>
            <tbody>
              {list.map((s) => {
                const system = s.manager.key === 'tekar-hosting' && !s.panel
                return (
                  <tr key={s.project}>
                    <td className="font-medium">{s.project}</td>
                    <td><ManagerBadge m={s.manager} /></td>
                    <td>{s.panel ? <Link to={`/apps/${s.panel.app_id}`}><Badge color={s.panel.kind === 'managed' ? 'green' : 'purple'}>{s.panel.kind === 'managed' ? 'ứng dụng' : 'quản lý tại chỗ'}: {s.panel.slug}</Badge></Link> : <span className="text-xs text-slate-400">—</span>}</td>
                    <td className="whitespace-nowrap">{s.running}/{s.containers.length}</td>
                    <td className="mono max-w-xs">{s.images.slice(0, 3).map((i) => <div key={i} className="truncate" title={i}>{i}</div>)}{s.images.length > 3 && <div className="text-slate-400">+{s.images.length - 3}</div>}</td>
                    <td className="mono max-w-xs break-all">{s.host_work_dir || s.work_dir}{s.host_work_dir && s.host_work_dir !== s.work_dir && <div className="text-slate-400">({s.work_dir})</div>}</td>
                    <td className="whitespace-nowrap text-right">
                      <button className="btn-ghost btn-sm" onClick={() => setView(s.project)}><Eye className="h-3.5 w-3.5" />Xem</button>
                      {!system && !s.panel && <button className="btn-secondary btn-sm" disabled={busy === s.project} onClick={() => adopt(s.project)}>{busy === s.project ? <Spinner /> : <Hand className="h-3.5 w-3.5" />}Quản lý tại chỗ</button>}
                      {!system && s.panel?.kind !== 'managed' && <button className="btn-primary btn-sm ml-1" onClick={() => setConvert(s)}><ArrowRightLeft className="h-3.5 w-3.5" />Chuyển về ứng dụng</button>}
                    </td>
                  </tr>
                )
              })}
              {q.data && !list.length && <tr><td colSpan={7} className="text-center text-slate-500">Không có stack</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
      <div className="mt-3 text-xs text-slate-500">
        "Quản lý tại chỗ": panel theo dõi, log, terminal, giới hạn tài nguyên, backup — công cụ cũ vẫn quản lý compose. "Chuyển về ứng dụng": panel sở hữu compose/env dạng thư mục chuẩn.
      </div>
      {view && <StackDrawer project={view} onClose={() => setView(null)} />}
      {convert && <ConvertModal stack={convert} onClose={() => setConvert(null)} onJob={setJob} />}
      <JobModal jobId={job} title="Chuyển stack về ứng dụng" onClose={() => { setJob(null); q.refetch(); qc.invalidateQueries({ queryKey: ['apps'] }) }} />
    </div>
  )
}
