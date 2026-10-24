import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { RotateCcw, Trash2 } from 'lucide-react'
import { api, type AppT, type Backup, type BackupTarget } from '../api'
import { Badge, ConfirmModal, ErrorBox, PageHeader, Spinner, StatusBadge } from '../components/ui'
import JobModal from '../components/JobModal'
import { useToast } from '../components/toast'
import { fmtBytes, fmtDate } from '../util'

export function BackupTable({ backups, loading, onChanged, runJob, canRestore, slugs, hideApp }: {
  backups: Backup[]; loading?: boolean; onChanged: () => void; runJob: (p: Promise<{ job_id: number }>, t?: string) => void
  canRestore: boolean | ((b: Backup) => boolean); slugs: Record<number, string>; hideApp?: boolean
}) {
  const toast = useToast()
  const [restore, setRestore] = useState<Backup | null>(null)
  const [items, setItems] = useState<Set<string>>(new Set())
  const [del, setDel] = useState<Backup | null>(null)
  const can = (b: Backup) => (typeof canRestore === 'function' ? canRestore(b) : canRestore)
  const targets = useQuery({ queryKey: ['backup-targets'], queryFn: () => api.get<BackupTarget[]>('/api/storage/targets') })
  const remote = (k: string) => {
    if (!k.startsWith('target:')) return { label: 'S3 (cũ)', title: k }
    const [, id, ...rest] = k.split(':')
    const t = targets.data?.find((x) => x.id === Number(id))
    return { label: t ? `${t.name} (${t.kind})` : `đích #${id}`, title: rest.join(':') }
  }
  if (loading) return <div className="p-4"><Spinner /></div>
  return (
    <div className="overflow-x-auto">
      <table className="table">
        <thead><tr><th>#</th>{!hideApp && <th>App</th>}<th>Trạng thái</th><th>Thời gian</th><th>Kích thước</th><th>Nội dung</th><th>Lưu trữ</th><th></th></tr></thead>
        <tbody>
          {backups.map((b) => (
            <tr key={b.id}>
              <td className="font-mono text-xs">{b.id}</td>
              {!hideApp && <td><Link to={`/apps/${b.app_id}/backup`} className="text-indigo-600">{b.app_name}</Link></td>}
              <td><StatusBadge status={b.status} /> <Badge color={b.trigger === 'import' ? 'purple' : 'gray'}>{b.trigger === 'schedule' ? 'lịch' : b.trigger === 'import' ? 'nhập từ đích' : 'tay'}</Badge>{b.error && <div className="mt-1 max-w-xs truncate text-xs text-rose-600" title={b.error}>{b.error}</div>}</td>
              <td className="whitespace-nowrap text-xs">{fmtDate(b.started_at)}</td>
              <td className="whitespace-nowrap">{fmtBytes(b.size_bytes)}</td>
              <td className="max-w-xs">
                <div className="flex flex-wrap gap-1">
                  {Object.entries((b.items || []).reduce((a: Record<string, number>, it: any) => { a[it.type] = (a[it.type] || 0) + 1; return a }, {})).map(([t, n]) => <Badge key={t}>{t}{(n as number) > 1 ? ` ×${n}` : ''}</Badge>)}
                </div>
              </td>
              <td className="text-xs"><div className="flex flex-wrap gap-1">{b.remote_key && <Badge color="purple" title={remote(b.remote_key).title}>{remote(b.remote_key).label}</Badge>}{(b.items?.length > 0 || !b.remote_key) && b.trigger !== 'import' && <Badge>local</Badge>}</div></td>
              <td className="whitespace-nowrap text-right">
                {b.status === 'success' && can(b) && <button className="btn-ghost btn-sm" title="Khôi phục" onClick={() => { setRestore(b); setItems(new Set((b.items || []).filter((i: any) => i.type !== 'config' && i.type !== 'sql').map((i: any) => i.file))) }}><RotateCcw className="h-3.5 w-3.5" /></button>}
                {can(b) && <button className="btn-ghost btn-sm" title="Xoá" onClick={() => setDel(b)}><Trash2 className="h-3.5 w-3.5" /></button>}
              </td>
            </tr>
          ))}
          {!backups.length && <tr><td colSpan={8} className="text-center text-slate-500">Chưa có backup</td></tr>}
        </tbody>
      </table>
      <ConfirmModal open={!!restore} onClose={() => setRestore(null)} title={`Khôi phục backup #${restore?.id}`} requireText={restore ? slugs[restore.app_id] : undefined}
        confirmText="Khôi phục"
        message={<span className="text-rose-600">App sẽ bị dừng, dữ liệu hiện tại của các mục được chọn sẽ bị ghi đè.</span>}
        onConfirm={async () => {
          const r = restore!
          setRestore(null)
          runJob(api.post(`/api/backups/${r.id}/restore`, { items: [...items], confirm: slugs[r.app_id] }), 'Khôi phục')
        }}>
        <div className="max-h-60 space-y-1 overflow-y-auto">
          {(restore?.items || []).filter((i: any) => i.type !== 'config' && i.type !== 'sql').map((it: any) => (
            <label key={it.file} className="flex items-start gap-2 text-xs">
              <input type="checkbox" checked={items.has(it.file)} onChange={(e) => { const n = new Set(items); e.target.checked ? n.add(it.file) : n.delete(it.file); setItems(n) }} />
              <span><Badge>{it.type}</Badge> <span className="font-mono">{it.name || it.source || it.resource}</span>{it.destination && <span className="text-slate-500"> → {it.destination}</span>}</span>
            </label>
          ))}
          {(restore?.items || []).some((i: any) => i.type === 'sql') && <div className="text-xs text-slate-500">Bản dump SQL (sql-*.sql.gz) được lưu để tham khảo / khôi phục thủ công trong thư mục backup.</div>}
        </div>
      </ConfirmModal>
      <ConfirmModal open={!!del} onClose={() => setDel(null)} title="Xoá backup" message={`Xoá backup #${del?.id} (cả bản local và bản trên đích)?`}
        onConfirm={async () => { try { await api.del(`/api/backups/${del!.id}`); toast.success('Đã xoá'); setDel(null); onChanged() } catch (e) { toast.error(e) } }} />
    </div>
  )
}

export default function Backups() {
  const [job, setJob] = useState<{ id: number; title: string } | null>(null)
  const toast = useToast()
  const q = useQuery({ queryKey: ['backups'], queryFn: () => api.get<Backup[]>('/api/backups?limit=200'), refetchInterval: 20000 })
  const apps = useQuery({ queryKey: ['apps'], queryFn: () => api.get<AppT[]>('/api/apps') })
  const slugs = Object.fromEntries((apps.data || []).map((a) => [a.id, a.slug]))
  const roles = Object.fromEntries((apps.data || []).map((a) => [a.id, a.my_role]))
  const noSchedule = (apps.data || []).filter((a) => !a.backup_cron)
  return (
    <div>
      <PageHeader title="Backup" subtitle="Toàn bộ bản backup của các ứng dụng" />
      {noSchedule.length > 0 && (
        <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          {noSchedule.length} app chưa có lịch backup: {noSchedule.map((a, i) => <span key={a.id}>{i > 0 && ', '}<Link to={`/apps/${a.id}/backup`} className="underline">{a.name}</Link></span>)}
        </div>
      )}
      <ErrorBox error={q.error} />
      <div className="card">
        <BackupTable backups={q.data || []} loading={q.isLoading} onChanged={() => q.refetch()} slugs={slugs} canRestore={(b) => roles[b.app_id] === 'owner'}
          runJob={(p, t) => p.then((r) => setJob({ id: r.job_id, title: t || '' })).catch((e) => toast.error(e))} />
      </div>
      <JobModal jobId={job?.id ?? null} title={job?.title} onClose={() => setJob(null)} />
    </div>
  )
}
