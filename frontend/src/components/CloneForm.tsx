import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'
import { api, type AppT } from '../api'
import { Badge, ErrorBox, Field, Loading, Spinner, Toggle } from './ui'
import { useToast } from './toast'

export function slugify(s: string) {
  return s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40)
}

/** Clone app đang chạy thành app managed mới. sourceId cố định (từ trang app) hoặc chọn trong danh sách. */
export default function CloneForm({ sourceId, onCreated, onCancel }: { sourceId?: number; onCreated: (id: number, job: number | null) => void; onCancel?: () => void }) {
  const toast = useToast()
  const apps = useQuery({ queryKey: ['apps'], queryFn: () => api.get<AppT[]>('/api/apps'), enabled: !sourceId })
  const [src, setSrc] = useState<number | null>(sourceId ?? null)
  const info = useQuery({ queryKey: ['clone-info', src], queryFn: () => api.get<any>(`/api/apps/${src}/clone-info`), enabled: !!src, retry: false })
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [o, setO] = useState({ copy_files: true, copy_data: false, stop_source: false, services: true, strip_ports: true, deploy: true })
  const [domain, setDomain] = useState('')
  const [web, setWeb] = useState({ service: '', port: '' })
  const [busy, setBusy] = useState(false)
  const d = info.data
  useEffect(() => {
    if (!d) return
    if (d.web) setWeb({ service: d.web.service, port: String(d.web.port) })
    else if (d.services?.length) setWeb({ service: d.services[0], port: '' })
    setSlug(slugify(`${d.slug}-copy`)); setName(`${d.name} (bản sao)`)
  }, [d])
  const opt = (k: keyof typeof o, label: React.ReactNode) => <div><Toggle checked={o[k]} onChange={(v) => setO({ ...o, [k]: v })} label={label} /></div>
  const usable = (apps.data || []).filter((a) => a.my_role === 'owner' || a.my_role === 'deployer')

  async function submit() {
    setBusy(true)
    try {
      const r = await api.post(`/api/apps/${src}/clone`, {
        name, slug, ...o, domain: domain || null,
        web_service: web.service || null, web_port: web.port ? Number(web.port) : null,
      })
      toast.success('Đã tạo bản sao, đang chép dữ liệu')
      onCreated(r.id, r.job_id)
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }

  return (
    <div className="space-y-3 text-sm">
      {!sourceId && (
        <Field label="App nguồn">
          <select className="input" value={src ?? ''} onChange={(e) => { setSrc(e.target.value ? Number(e.target.value) : null); setSlug(''); setName('') }}>
            <option value="">— chọn app —</option>
            {usable.map((a) => <option key={a.id} value={a.id}>{a.name} · {a.compose_project} ({a.kind}, {a.status})</option>)}
          </select>
        </Field>
      )}
      {src && info.isLoading && <Loading />}
      <ErrorBox error={info.error} />
      {d && (
        <>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Tên app mới"><input className="input" value={name} onChange={(e) => { setName(e.target.value); if (!slug || slug === slugify(name)) setSlug(slugify(e.target.value)) }} /></Field>
            <Field label="Slug" hint="project compose: th-<slug>"><input className="input font-mono" value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} /></Field>
          </div>
          <div className="flex flex-wrap gap-1 text-xs">
            <span className="text-slate-500">Service:</span>{d.services.map((s: string) => <Badge key={s}>{s}</Badge>)}
            {d.git && <Badge color="purple">git</Badge>}
            {d.bindings.map((b: any) => <Badge key={b.resource + b.kind} color="blue">{b.kind}: {b.resource}</Badge>)}
          </div>
          <div className="space-y-1.5">
            {opt('copy_files', 'Chép thư mục app (file cấu hình, code, thư mục bind tương đối)')}
            {opt('copy_data', <>Chép dữ liệu: volume{d.volumes.length ? ` (${d.volumes.join(', ')})` : ''}{d.data_dir ? ', APP_DATA_DIR' : ''}{d.bindings.length ? ', database/redis/bucket' : ''}</>)}
            {o.copy_data && <div className="pl-6">{opt('stop_source', 'Tạm dừng app gốc khi chép (dữ liệu nhất quán, app gốc gián đoạn vài giây – vài phút)')}</div>}
            {d.bindings.length > 0 && opt('services', 'Cấp database/redis/bucket riêng cho bản sao trên cùng dịch vụ nền tảng')}
            {opt('strip_ports', <>Bỏ cổng publish ra host{d.ports.length ? <span className="text-slate-500"> — {d.ports.map((p: any) => `${p.service}: ${p.ports.join(', ')}`).join(' · ')}</span> : ''}</>)}
            {opt('deploy', 'Deploy ngay sau khi chép')}
          </div>
          {(d.container_names.length > 0 || d.shared_binds.length > 0 || (o.copy_data && !o.stop_source) || (!o.strip_ports && d.ports.length > 0)) && (
            <div className="space-y-0.5 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
              {d.container_names.length > 0 && <div><AlertTriangle className="mr-1 inline h-3.5 w-3.5" />Bỏ container_name của {d.container_names.join(', ')} để không trùng tên.</div>}
              {d.shared_binds.map((b: any) => <div key={b.path}><AlertTriangle className="mr-1 inline h-3.5 w-3.5" />{b.service} mount đường dẫn tuyệt đối <span className="font-mono">{b.path}</span> — bản sao dùng chung thư mục này với app gốc.</div>)}
              {o.copy_data && !o.stop_source && <div><AlertTriangle className="mr-1 inline h-3.5 w-3.5" />Chép khi app gốc đang chạy: database chạy trong container (không phải dịch vụ nền tảng) có thể không nhất quán.</div>}
              {!o.strip_ports && d.ports.length > 0 && <div><AlertTriangle className="mr-1 inline h-3.5 w-3.5" />Giữ cổng publish: bản sao sẽ lỗi trùng cổng nếu app gốc đang chạy.</div>}
            </div>
          )}
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Domain cho bản sao (tuỳ chọn)"><input className="input font-mono" value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="staging.example.com" /></Field>
            <Field label="Service web">
              <select className="input" value={web.service} onChange={(e) => setWeb({ ...web, service: e.target.value })}>{d.services.map((s: string) => <option key={s}>{s}</option>)}</select>
            </Field>
            <Field label="Cổng container"><input className="input font-mono" value={web.port} onChange={(e) => setWeb({ ...web, port: e.target.value.replace(/\D/g, '') })} placeholder="80" /></Field>
          </div>
          <div className="flex justify-end gap-2">
            {onCancel && <button className="btn-secondary" onClick={onCancel}>Huỷ</button>}
            <button className="btn-primary" disabled={busy || !name || !slug || (!!domain && !web.port)} onClick={submit}>{busy && <Spinner />}Clone{o.deploy ? ' & deploy' : ''}</button>
          </div>
        </>
      )}
    </div>
  )
}
