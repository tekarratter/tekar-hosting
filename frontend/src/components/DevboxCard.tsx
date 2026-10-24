import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Eye, KeyRound, Pencil, RefreshCw } from 'lucide-react'
import { api, type AppT } from '../api'
import { Badge, Code, CopyButton, Field, Modal, Section, Spinner, Toggle } from './ui'
import { useToast } from './toast'

export function genPassword(n = 20) {
  const a = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
  const r = crypto.getRandomValues(new Uint32Array(n))
  return Array.from(r, (x) => a[x % a.length]).join('')
}

interface Info { user: string; port: number | null; host: string | null; password_auth: boolean; sudo: boolean; keys: string; workspace: string; host_dir: string; password?: string }

export default function DevboxCard({ app, canDeploy, runJob }: { app: AppT; canDeploy: boolean; runJob: (p: Promise<{ job_id: number }>, title?: string) => void }) {
  const toast = useToast()
  const q = useQuery({ queryKey: ['devbox', app.id], queryFn: () => api.get<Info>(`/api/apps/${app.id}/devbox`) })
  const [pw, setPw] = useState<string | null>(null)
  const [edit, setEdit] = useState(false)
  const [f, setF] = useState({ pwMode: 'keep', password: '', keys: '', sudo: false })
  const [busy, setBusy] = useState(false)
  const d = q.data
  if (!d) return null
  const host = d.host || window.location.hostname
  const cmd = `ssh ${d.port && d.port !== 22 ? `-p ${d.port} ` : ''}${d.user}@${host}`
  const sshCfg = `Host ${app.slug}\n  HostName ${host}\n  Port ${d.port}\n  User ${d.user}\n`
  const nKeys = d.keys.split('\n').filter((l) => l.trim()).length

  async function save() {
    setBusy(true)
    try {
      const body: any = { keys: f.keys, sudo: f.sudo }
      if (f.pwMode === 'set') body.password = f.password
      if (f.pwMode === 'off') body.password = ''
      const p = api.put(`/api/apps/${app.id}/devbox`, body)
      await p
      setEdit(false); setPw(null); q.refetch()
      runJob(p, 'Cập nhật SSH')
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }

  return (
    <Section title={<span className="flex items-center gap-2"><KeyRound className="h-4 w-4 text-indigo-500" />Truy cập SSH</span>}
      actions={canDeploy && <button className="btn-secondary btn-sm" onClick={() => { setF({ pwMode: 'keep', password: genPassword(), keys: d.keys, sudo: d.sudo }); setEdit(true) }}><Pencil className="h-3.5 w-3.5" />Sửa</button>}>
      <div className="space-y-3 p-4 text-sm">
        <Code>{cmd}</Code>
        {!d.host && <div className="text-xs text-amber-600">Chưa đặt host SSH — đang hiển thị tên miền của panel. Đặt IP/tên miền trỏ thẳng về máy chủ ở Cài đặt → Môi trường phát triển.</div>}
        <div className="grid gap-x-6 gap-y-1 md:grid-cols-2">
          <div>User: <span className="font-mono">{d.user}</span> {d.sudo && <Badge color="orange">sudo</Badge>}</div>
          <div>Cổng: <span className="font-mono">{d.port ?? '—'}</span></div>
          <div className="flex items-center gap-1">Mật khẩu: {!d.password_auth ? <span className="text-slate-500">tắt (chỉ dùng key)</span> : pw !== null ? <><span className="font-mono">{pw}</span><CopyButton text={pw} /></> : <>
            <span className="font-mono">••••••••</span>
            {canDeploy && <button className="btn-ghost btn-sm" title="Hiện mật khẩu" onClick={async () => { try { setPw((await api.get<Info>(`/api/apps/${app.id}/devbox?reveal=true`)).password || '') } catch (e) { toast.error(e) } }}><Eye className="h-3.5 w-3.5" /></button>}
          </>}</div>
          <div>Public key: {nKeys ? `${nKeys} key` : <span className="text-slate-500">chưa có</span>}</div>
          <div>Thư mục làm việc: <span className="font-mono">{d.workspace}</span></div>
          <div className="truncate" title={d.host_dir}>Trên host: <span className="font-mono text-xs">{d.host_dir}</span></div>
        </div>
        <div className="rounded-md bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-900 dark:text-slate-400">
          Thông tin dịch vụ dùng chung (<span className="font-mono">DATABASE_URL, PG*, REDIS_URL, S3_*…</span>) có sẵn dưới dạng biến môi trường trong mọi phiên SSH và trong file <span className="font-mono">~/services.env</span> (chép thành <span className="font-mono">.env</span> cho dự án). Thêm/bớt dịch vụ ở tab <Link className="text-indigo-600" to={`/apps/${app.id}/services`}>Dịch vụ</Link> rồi Deploy lại.
        </div>
        <details className="text-xs"><summary className="cursor-pointer text-slate-500">Cấu hình ~/.ssh/config (VS Code Remote-SSH, JetBrains Gateway)</summary>
          <Code className="mt-2">{sshCfg}</Code>
        </details>
      </div>
      <Modal open={edit} onClose={() => setEdit(false)} title="Sửa truy cập SSH" footer={<>
        <button className="btn-secondary" onClick={() => setEdit(false)}>Huỷ</button>
        <button className="btn-primary" disabled={busy} onClick={save}>{busy && <Spinner />}Lưu & áp dụng</button>
      </>}>
        <div className="space-y-3 text-sm">
          <Field label="Mật khẩu">
            <div className="space-y-2">
              <select className="input" value={f.pwMode} onChange={(e) => setF({ ...f, pwMode: e.target.value })}>
                <option value="keep">Giữ nguyên{d.password_auth ? '' : ' (đang tắt)'}</option>
                <option value="set">Đặt mật khẩu mới</option>
                <option value="off">Tắt đăng nhập bằng mật khẩu</option>
              </select>
              {f.pwMode === 'set' && <div className="flex gap-2"><input className="input font-mono" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /><button className="btn-secondary" title="Sinh ngẫu nhiên" onClick={() => setF({ ...f, password: genPassword() })}><RefreshCw className="h-4 w-4" /></button></div>}
            </div>
          </Field>
          <Field label="Public key (mỗi dòng một key)" hint="Lưu ở authorized_keys trong thư mục app. Người dùng cũng có thể tự thêm key vào ~/.ssh/authorized_keys.">
            <textarea className="input font-mono text-xs" rows={5} value={f.keys} onChange={(e) => setF({ ...f, keys: e.target.value })} placeholder="ssh-ed25519 AAAA… user@laptop" spellCheck={false} />
          </Field>
          <Toggle checked={f.sudo} onChange={(v) => setF({ ...f, sudo: v })} label="Cho phép sudo không cần mật khẩu (cài thêm gói bằng apt)" />
          <div className="text-xs text-slate-500">Lưu sẽ tạo lại container; dữ liệu trong /home giữ nguyên, các phiên SSH đang mở bị ngắt.</div>
        </div>
      </Modal>
    </Section>
  )
}
