import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Database, Trash2, KeyRound, Sparkles } from 'lucide-react'
import { api, type PlatformService } from '../api'
import { Badge, ConfirmModal, Drawer, Empty, ErrorBox, Field, Loading, Modal, PageHeader, Section, Spinner, Toggle, Code } from '../components/ui'
import { useToast } from '../components/toast'
import { fmtBytes, fmtDate } from '../util'

const DEFAULT_PORT: Record<string, number> = { postgres: 5432, redis: 6379, s3: 9000 }

function ServiceForm({ open, onClose, svc, onSaved }: { open: boolean; onClose: () => void; svc?: PlatformService | null; onSaved: () => void }) {
  const toast = useToast()
  const [f, setF] = useState<any>(() => svc ? { ...svc, admin_password: '', extra: svc.extra || {} } : { name: '', kind: 'postgres', host: 'host.docker.internal', port: 5432, public_host: '', public_port: '', admin_user: 'postgres', admin_password: '', extra: {} })
  const [busy, setBusy] = useState(false)
  const set = (k: string, v: any) => setF({ ...f, [k]: v })
  const setX = (k: string, v: any) => setF({ ...f, extra: { ...f.extra, [k]: v } })
  async function save() {
    setBusy(true)
    try {
      const body = { ...f, port: Number(f.port), public_port: f.public_port ? Number(f.public_port) : null, public_host: f.public_host || null }
      if (svc) await api.put(`/api/platform/services/${svc.id}`, body)
      else await api.post('/api/platform/services', body)
      toast.success('Đã lưu — kết nối thành công'); onSaved(); onClose()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }
  return (
    <Modal open={open} onClose={onClose} title={svc ? `Sửa ${svc.name}` : 'Thêm dịch vụ có sẵn'} footer={<>
      <button className="btn-secondary" onClick={onClose}>Huỷ</button>
      <button className="btn-primary" disabled={busy} onClick={save}>{busy && <Spinner />}Kiểm tra & lưu</button>
    </>}>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Tên"><input className="input" value={f.name} onChange={(e) => set('name', e.target.value)} /></Field>
        <Field label="Loại">
          <select className="input" disabled={!!svc} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value, port: DEFAULT_PORT[e.target.value], admin_user: e.target.value === 'postgres' ? 'postgres' : e.target.value === 'redis' ? 'default' : '' })}>
            <option value="postgres">PostgreSQL</option><option value="redis">Redis</option><option value="s3">S3 / MinIO</option>
          </select>
        </Field>
        <Field label="Host (panel kết nối)" hint="host.docker.internal = cổng trên host; hoặc tên container cùng mạng"><input className="input font-mono" value={f.host} onChange={(e) => set('host', e.target.value)} /></Field>
        <Field label="Port"><input className="input" type="number" value={f.port} onChange={(e) => set('port', e.target.value)} /></Field>
        <Field label="Host cho app (tuỳ chọn)" hint="Nếu app kết nối bằng địa chỉ khác"><input className="input font-mono" value={f.public_host || ''} onChange={(e) => set('public_host', e.target.value)} /></Field>
        <Field label="Port cho app (tuỳ chọn)"><input className="input" type="number" value={f.public_port || ''} onChange={(e) => set('public_port', e.target.value)} /></Field>
        <Field label={f.kind === 's3' ? 'Access key (admin)' : 'User admin'}><input className="input font-mono" value={f.admin_user} onChange={(e) => set('admin_user', e.target.value)} /></Field>
        <Field label={f.kind === 's3' ? 'Secret key' : 'Mật khẩu admin'}><input className="input" type="password" placeholder={svc ? '(giữ nguyên)' : ''} value={f.admin_password} onChange={(e) => set('admin_password', e.target.value)} /></Field>
        {f.kind === 's3' && (
          <>
            <Field label="Provider"><select className="input" value={f.extra.provider || 'minio'} onChange={(e) => setX('provider', e.target.value)}><option value="minio">MinIO (tạo user/policy riêng)</option><option value="other">S3 khác (chỉ tạo bucket)</option></select></Field>
            <Field label="Region"><input className="input" value={f.extra.region || ''} onChange={(e) => setX('region', e.target.value)} placeholder="us-east-1" /></Field>
            <Field label="Public endpoint cho app" hint="vd https://s3.example.com"><input className="input font-mono" value={f.extra.public_endpoint || ''} onChange={(e) => setX('public_endpoint', e.target.value)} /></Field>
            <Field label="Bucket chứa backup"><input className="input font-mono" value={f.extra.backup_bucket || ''} onChange={(e) => setX('backup_bucket', e.target.value)} placeholder="th-backups" /></Field>
            <div className="md:col-span-2"><Toggle checked={!!f.extra.secure} onChange={(v) => setX('secure', v)} label="HTTPS" /></div>
          </>
        )}
      </div>
    </Modal>
  )
}

function Detail({ id, onClose, onEdit }: { id: number; onClose: () => void; onEdit: (s: PlatformService) => void }) {
  const toast = useToast()
  const q = useQuery({ queryKey: ['platform-service', id], queryFn: () => api.get(`/api/platform/services/${id}`) })
  const [cred, setCred] = useState<any>(null)
  const d = q.data
  return (
    <Drawer open onClose={onClose} title={d ? `${d.name} (${d.kind})` : 'Dịch vụ'}>
      {q.isLoading && <Loading />}
      {d && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {d.online ? <Badge color="green">online</Badge> : <Badge color="red">offline</Badge>}
            <Badge>{d.mode}</Badge><span className="font-mono">{d.host}:{d.port}</span>
            {d.container_name && <span className="text-xs text-slate-500">container {d.container_name}</span>}
            <div className="ml-auto flex gap-2">
              <button className="btn-secondary btn-sm" onClick={() => onEdit(d)}>Sửa</button>
              <button className="btn-secondary btn-sm" onClick={async () => { try { setCred(await api.get(`/api/platform/services/${id}/credentials`)) } catch (e) { toast.error(e) } }}><KeyRound className="h-3.5 w-3.5" />Thông tin admin</button>
            </div>
          </div>
          {cred && <Code>{`user: ${cred.admin_user}\npassword: ${cred.admin_password}`}</Code>}
          {!d.online && <ErrorBox error={d.info?.error} />}
          {d.online && d.kind === 'postgres' && (
            <Section title={`PostgreSQL ${d.info.version} · ${d.info.connections}/${d.info.max_connections} kết nối`}>
              <table className="table"><tbody>{d.info.databases.map((x: any) => <tr key={x.name}><td className="mono">{x.name}</td><td className="text-right">{fmtBytes(x.size)}</td></tr>)}</tbody></table>
            </Section>
          )}
          {d.online && d.kind === 'redis' && (
            <Section title={`Redis ${d.info.version}`}>
              <div className="space-y-1 p-4 text-sm">
                <div>RAM: {fmtBytes(d.info.used_memory)}{d.info.maxmemory ? ` / ${fmtBytes(d.info.maxmemory)}` : ''} · {d.info.connected_clients} client · {d.info.keys} key</div>
                {!d.info.acl_persistent && <div className="rounded border border-amber-300 bg-amber-50 p-2 text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">Redis không dùng aclfile: ACL user cấp cho app sẽ mất khi Redis restart. Cấu hình <span className="font-mono">--aclfile</span> hoặc dùng Redis managed.</div>}
              </div>
            </Section>
          )}
          {d.online && d.kind === 's3' && (
            <Section title="Buckets">
              <table className="table"><tbody>{d.info.buckets.map((b: any) => <tr key={b.name}><td className="mono">{b.name}</td><td className="text-xs text-slate-500">{fmtDate(b.created)}</td></tr>)}</tbody></table>
            </Section>
          )}
          <Section title="App đang dùng">
            <table className="table"><tbody>
              {d.bindings.map((b: any) => <tr key={b.id}><td>{b.app}</td><td className="mono">{b.resource}</td><td className="mono">{b.username}</td></tr>)}
              {!d.bindings.length && <tr><td className="text-slate-500">Chưa có</td></tr>}
            </tbody></table>
          </Section>
        </div>
      )}
    </Drawer>
  )
}

export default function Platform() {
  const toast = useToast()
  const q = useQuery({ queryKey: ['platform-services'], queryFn: () => api.get<PlatformService[]>('/api/platform/services') })
  const [form, setForm] = useState<PlatformService | null | undefined>(undefined)
  const [managed, setManaged] = useState(false)
  const [mName, setMName] = useState('')
  const [mKind, setMKind] = useState('postgres')
  const [busy, setBusy] = useState(false)
  const [detail, setDetail] = useState<number | null>(null)
  const [del, setDel] = useState<PlatformService | null>(null)
  const [rmContainer, setRmContainer] = useState(false)

  return (
    <div>
      <PageHeader title="Dịch vụ nền tảng" subtitle="Postgres / Redis / S3 dùng chung — mỗi app được cấp tài nguyên & tài khoản riêng" actions={<>
        <button className="btn-secondary" onClick={() => setManaged(true)}><Sparkles className="h-4 w-4" />Tạo mới (managed)</button>
        <button className="btn-primary" onClick={() => setForm(null)}><Plus className="h-4 w-4" />Thêm dịch vụ có sẵn</button>
      </>} />
      {q.isLoading && <Loading />}
      <ErrorBox error={q.error} />
      {q.data && !q.data.length && <div className="card"><Empty><Database className="mx-auto mb-2 h-8 w-8 text-slate-300" />Chưa có dịch vụ. Thêm Postgres/Redis/MinIO đang chạy (vd baogia_db, redis_db) hoặc tạo mới.</Empty></div>}
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(q.data || []).map((s) => (
          <div key={s.id} className="card cursor-pointer p-4 hover:border-indigo-400" onClick={() => setDetail(s.id)}>
            <div className="flex items-center gap-2"><Badge color="purple">{s.kind}</Badge><span className="font-semibold">{s.name}</span><Badge>{s.mode}</Badge>
              <button className="btn-ghost btn-sm ml-auto" onClick={(e) => { e.stopPropagation(); setDel(s); setRmContainer(false) }}><Trash2 className="h-3.5 w-3.5" /></button>
            </div>
            <div className="mt-2 font-mono text-xs text-slate-500">{s.host}:{s.port}</div>
            <div className="mt-1 text-xs text-slate-500">{s.bindings} app đang dùng</div>
          </div>
        ))}
      </div>
      {form !== undefined && <ServiceForm open svc={form} onClose={() => setForm(undefined)} onSaved={() => q.refetch()} />}
      {detail != null && <Detail id={detail} onClose={() => setDetail(null)} onEdit={(s) => { setDetail(null); setForm(s) }} />}
      <Modal open={managed} onClose={() => setManaged(false)} title="Tạo dịch vụ managed" size="sm" footer={<>
        <button className="btn-secondary" onClick={() => setManaged(false)}>Huỷ</button>
        <button className="btn-primary" disabled={!mName || busy} onClick={async () => {
          setBusy(true)
          try { await api.post('/api/platform/services/managed', { name: mName, kind: mKind }); toast.success('Đã tạo container dịch vụ'); setManaged(false); q.refetch() } catch (e) { toast.error(e) } finally { setBusy(false) }
        }}>{busy && <Spinner />}Tạo</button>
      </>}>
        <div className="space-y-3">
          <Field label="Loại"><select className="input" value={mKind} onChange={(e) => setMKind(e.target.value)}><option value="postgres">PostgreSQL 17</option><option value="redis">Redis 7 (có aclfile)</option><option value="s3">MinIO</option></select></Field>
          <Field label="Tên"><input className="input" value={mName} onChange={(e) => setMName(e.target.value)} placeholder="main" /></Field>
          <div className="text-xs text-slate-500">Panel tạo container trên mạng th-edge với mật khẩu ngẫu nhiên và volume riêng.</div>
        </div>
      </Modal>
      <ConfirmModal open={!!del} onClose={() => setDel(null)} title={`Xoá ${del?.name}`} requireText={del?.name}
        message="Chỉ xoá được khi không còn app nào dùng."
        onConfirm={async () => { try { await api.del(`/api/platform/services/${del!.id}?remove_container=${rmContainer}`); toast.success('Đã xoá'); setDel(null); q.refetch() } catch (e) { toast.error(e) } }}>
        {del?.container_name && <Toggle checked={rmContainer} onChange={setRmContainer} label="Xoá luôn container managed" />}
      </ConfirmModal>
    </div>
  )
}
