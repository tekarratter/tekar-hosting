import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { GitPullRequestArrow, RefreshCw, Unlink } from 'lucide-react'
import { api } from '../../api'
import { Badge, ErrorBox, Field, Loading, Section, Spinner, Toggle } from '../../components/ui'
import { useToast } from '../../components/toast'
import { GitPicker, type GitSel } from '../../components/Git'
import { fmtAgo } from '../../util'
import type { TabProps } from '../AppDetail'

export default function GitTab({ app, refetch, runJob }: TabProps) {
  const toast = useToast()
  const q = useQuery({ queryKey: ['app-git', app.id], queryFn: () => api.get<any>(`/api/apps/${app.id}/git`) })
  const g = app.git
  const [edit, setEdit] = useState(!g)
  const [sel, setSel] = useState<GitSel>({ repo: g?.repo || '', branch: g?.branch || '', credential_id: g?.credential_id ?? null, subdir: g?.subdir || '' })
  const [composePath, setComposePath] = useState(g?.compose_path || '')
  const [sync, setSync] = useState(!!g?.sync_compose)
  const [autoPull, setAutoPull] = useState(!!g?.auto_pull)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setEdit(!g) }, [g?.repo]) // eslint-disable-line

  async function save() {
    setBusy(true)
    try {
      await api.put(`/api/apps/${app.id}/git`, { ...sel, compose_path: composePath || null, sync_compose: sync, auto_pull: autoPull })
      toast.success('Đã lưu'); setEdit(false); refetch(); q.refetch()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }
  const d = q.data

  return (
    <div className="space-y-4">
      {g && (
        <Section title="Nguồn code" actions={<div className="flex gap-2">
          <button className="btn-secondary btn-sm" onClick={() => q.refetch()}>{q.isFetching ? <Spinner /> : <RefreshCw className="h-3.5 w-3.5" />}Kiểm tra</button>
          <button className="btn-secondary btn-sm" onClick={() => runJob(api.post(`/api/apps/${app.id}/git/pull?deploy=false`), 'Kéo code')}>Chỉ kéo code</button>
          <button className="btn-primary btn-sm" onClick={() => runJob(api.post(`/api/apps/${app.id}/git/pull`), 'Kéo code & deploy')}><GitPullRequestArrow className="h-3.5 w-3.5" />Kéo code & deploy</button>
        </div>}>
          <div className="grid gap-3 p-4 text-sm md:grid-cols-2">
            <div><div className="text-xs text-slate-500">Repo</div><a className="font-mono text-indigo-600 dark:text-indigo-400" href={g.repo} target="_blank" rel="noreferrer">{g.repo}</a>{g.subdir && <span className="font-mono text-slate-500"> /{g.subdir}</span>}</div>
            <div><div className="text-xs text-slate-500">Nhánh · kết nối</div><span className="font-mono">{g.branch || 'mặc định'}</span> · {d?.credential ? d.credential.name : g.credential_id ? <Badge color="red">kết nối đã xoá</Badge> : 'public'}</div>
            <div>
              <div className="text-xs text-slate-500">Commit đang chạy</div>
              {g.commit ? <><span className="font-mono">{g.commit.slice(0, 10)}</span> {g.commit_msg} <span className="text-xs text-slate-500">· {g.author} · kéo {fmtAgo(g.pulled_at)}</span></> : <span className="text-slate-500">chưa clone</span>}
            </div>
            <div>
              <div className="text-xs text-slate-500">Trên remote</div>
              {q.isLoading ? <Spinner /> : d?.remote_error ? <span className="text-rose-600">{d.remote_error}</span>
                : d?.remote_commit ? <><span className="font-mono">{d.remote_commit.slice(0, 10)}</span> {d.behind ? <Badge color="yellow">có commit mới</Badge> : <Badge color="green">đã mới nhất</Badge>}</> : '—'}
            </div>
            <div className="md:col-span-2 flex flex-wrap gap-2 text-xs">
              {g.compose_path ? <Badge color={g.sync_compose ? 'blue' : 'gray'}>compose: {g.compose_path}{g.sync_compose ? ' (đồng bộ)' : ''}</Badge> : <Badge>compose do panel quản lý</Badge>}
              {g.auto_pull && <Badge color="purple">tự kéo code 5 phút/lần</Badge>}
              {g.error && <span className="text-rose-600">Lỗi lần kiểm tra gần nhất: {g.error}</span>}
            </div>
            <div className="md:col-span-2 text-xs text-slate-500">
              Code nằm ở <span className="font-mono">{app.work_dir}/src</span>. Kéo code = fetch + reset về commit mới nhất của nhánh (sửa tay trong src sẽ mất, file không thuộc repo giữ nguyên), rồi <span className="font-mono">docker compose up -d --build</span>.
              {' '}Deploy khi push: bật <Link className="underline" to={`/apps/${app.id}/webhook`}>Webhook</Link> rồi thêm URL vào GitHub → Settings → Webhooks (content type <span className="font-mono">application/json</span>, sự kiện push); push nhánh khác được bỏ qua.
            </div>
          </div>
        </Section>
      )}
      {q.error && <ErrorBox error={q.error} />}
      {app.kind !== 'managed' ? (
        <Section title="Git"><div className="p-4 text-sm text-slate-500">App adopted không gắn được nguồn git. Chuyển stack sang panel hoặc clone thành app managed.</div></Section>
      ) : edit ? (
        <Section title={g ? 'Cấu hình git' : 'Gắn repo git cho app'}>
          <div className="space-y-3 p-4 text-sm">
            {!g && <p className="text-slate-600 dark:text-slate-400">Code sẽ được clone vào <span className="font-mono">{app.work_dir}/src</span> ở lần kéo đầu tiên. Compose của app cần trỏ vào <span className="font-mono">./src</span> (vd <span className="font-mono">build: ./src</span>), hoặc chọn file compose trong repo để panel đồng bộ.</p>}
            <GitPicker value={sel} onChange={setSel} />
            <Field label="File compose trong repo (tuỳ chọn)" hint="vd docker-compose.yml — trống: giữ compose hiện tại của app">
              <input className="input font-mono" value={composePath} onChange={(e) => setComposePath(e.target.value)} />
            </Field>
            {composePath && <Toggle checked={sync} onChange={setSync} label="Ghi đè docker-compose.yml của app theo file này mỗi lần kéo code" />}
            <Toggle checked={autoPull} onChange={setAutoPull} label="Tự kéo code mới & deploy khi nhánh có commit mới (5 phút/lần)" />
            <div className="flex gap-2">
              <button className="btn-primary" disabled={busy || !sel.repo} onClick={save}>{busy && <Spinner />}Lưu</button>
              {g && <button className="btn-secondary" onClick={() => setEdit(false)}>Huỷ</button>}
            </div>
          </div>
        </Section>
      ) : (
        <div className="flex gap-2">
          <button className="btn-secondary" onClick={() => setEdit(true)}>Sửa cấu hình git</button>
          <button className="btn-secondary" onClick={async () => {
            if (!confirm('Gỡ nguồn git? Thư mục src và compose giữ nguyên.')) return
            try { await api.del(`/api/apps/${app.id}/git`); toast.success('Đã gỡ'); refetch() } catch (e) { toast.error(e) }
          }}><Unlink className="h-4 w-4" />Gỡ nguồn git</button>
        </div>
      )}
      {q.isLoading && !g && <Loading />}
    </div>
  )
}
