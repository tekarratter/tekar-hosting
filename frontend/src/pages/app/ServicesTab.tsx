import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, KeyRound, Trash2, Eye } from 'lucide-react'
import { api, type PlatformService } from '../../api'
import { Badge, ConfirmModal, Field, Modal, Section, Spinner, Toggle, Code } from '../../components/ui'
import { useToast } from '../../components/toast'
import { fmtDate } from '../../util'
import type { TabProps } from '../AppDetail'

export default function ServicesTab({ app, canDeploy, isOwner }: TabProps) {
  const toast = useToast()
  const [reveal, setReveal] = useState(false)
  const q = useQuery({ queryKey: ['bindings', app.id, reveal], queryFn: () => api.get<any[]>(`/api/platform/apps/${app.id}/bindings${reveal ? '?reveal=true' : ''}`) })
  const svcs = useQuery({ queryKey: ['platform-services'], queryFn: () => api.get<PlatformService[]>('/api/platform/services') })
  const [add, setAdd] = useState(false)
  const [sid, setSid] = useState<number | ''>('')
  const [prefix, setPrefix] = useState('')
  const [resource, setResource] = useState('')
  const [busy, setBusy] = useState(false)
  const [del, setDel] = useState<any>(null)
  const [drop, setDrop] = useState(false)

  async function create() {
    setBusy(true)
    try {
      await api.post(`/api/platform/apps/${app.id}/bindings`, { service_id: sid, env_prefix: prefix, resource: resource || null })
      toast.success('Đã cấp tài nguyên — deploy lại app để nhận biến môi trường')
      setAdd(false); q.refetch()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }

  const envText = (q.data || []).map((b) => Object.entries(b.env).map(([k, v]) => `${k}=${v}`).join('\n')).join('\n')

  return (
    <div className="space-y-4">
      <Section title="Dịch vụ nền tảng đã cấp" actions={<>
        {canDeploy && <button className="btn-secondary btn-sm" onClick={() => setReveal(!reveal)}><Eye className="h-3.5 w-3.5" />{reveal ? 'Ẩn' : 'Hiện'} secret</button>}
        {isOwner && <button className="btn-primary btn-sm" onClick={() => setAdd(true)}><Plus className="h-3.5 w-3.5" />Cấp mới</button>}
      </>}>
        <table className="table">
          <thead><tr><th>Loại</th><th>Dịch vụ</th><th>Tài nguyên</th><th>User</th><th>Tiền tố env</th><th>Backup</th><th></th></tr></thead>
          <tbody>
            {(q.data || []).map((b) => (
              <tr key={b.id}>
                <td><Badge color="purple">{b.kind}</Badge></td>
                <td>{b.service}</td>
                <td className="mono">{b.resource}</td>
                <td className="mono">{b.username}</td>
                <td className="mono">{b.env_prefix || '—'}</td>
                <td><Toggle checked={b.include_in_backup} onChange={async (v) => { if (!canDeploy) return; try { await api.put(`/api/platform/apps/${app.id}/bindings/${b.id}`, { include_in_backup: v }); q.refetch() } catch (e) { toast.error(e) } }} /></td>
                <td className="whitespace-nowrap text-right">
                  {isOwner && <button className="btn-ghost btn-sm" title="Đổi mật khẩu" onClick={async () => { try { const r = await api.post(`/api/platform/apps/${app.id}/bindings/${b.id}/rotate`); toast.success(r.note) } catch (e) { toast.error(e) } }}><KeyRound className="h-3.5 w-3.5" /></button>}
                  {isOwner && <button className="btn-ghost btn-sm" title="Thu hồi" onClick={() => { setDel(b); setDrop(false) }}><Trash2 className="h-3.5 w-3.5" /></button>}
                </td>
              </tr>
            ))}
            {!q.data?.length && <tr><td colSpan={7} className="text-center text-slate-500">Chưa dùng dịch vụ nền tảng nào</td></tr>}
          </tbody>
        </table>
      </Section>
      {(q.data || []).length > 0 && (
        <Section title="Biến môi trường được tiêm">
          <div className="p-4">
            <Code>{envText}</Code>
            <div className="mt-2 text-xs text-slate-500">{app.kind === 'managed' ? 'Tự động thêm vào .env khi deploy.' : 'App adopted: thêm thủ công các biến này vào .env của dự án.'} Cấp ngày {fmtDate(q.data?.[0]?.created_at)}.</div>
          </div>
        </Section>
      )}
      <Modal open={add} onClose={() => setAdd(false)} title="Cấp dịch vụ nền tảng" size="sm" footer={<>
        <button className="btn-secondary" onClick={() => setAdd(false)}>Huỷ</button>
        <button className="btn-primary" disabled={!sid || busy} onClick={create}>{busy && <Spinner />}Cấp</button>
      </>}>
        <div className="space-y-3">
          <Field label="Dịch vụ">
            <select className="input" value={sid} onChange={(e) => setSid(e.target.value ? Number(e.target.value) : '')}>
              <option value="">— chọn —</option>
              {(svcs.data || []).map((s) => <option key={s.id} value={s.id}>{s.kind} · {s.name}</option>)}
            </select>
          </Field>
          <Field label="Tên tài nguyên (tuỳ chọn)" hint="Mặc định theo slug app (database / key prefix / bucket)"><input className="input font-mono" value={resource} onChange={(e) => setResource(e.target.value)} placeholder={app.slug} /></Field>
          <Field label="Tiền tố biến môi trường (tuỳ chọn)" hint="vd ANALYTICS_ → ANALYTICS_DATABASE_URL"><input className="input font-mono" value={prefix} onChange={(e) => setPrefix(e.target.value.toUpperCase())} /></Field>
        </div>
      </Modal>
      <ConfirmModal open={!!del} onClose={() => setDel(null)} title="Thu hồi tài nguyên" requireText={drop ? del?.resource : undefined}
        message={<>Thu hồi <b>{del?.resource}</b> trên {del?.service}. User sẽ bị xoá/khoá.</>}
        onConfirm={async () => { try { await api.del(`/api/platform/apps/${app.id}/bindings/${del.id}?drop_data=${drop}`); toast.success('Đã thu hồi'); setDel(null); q.refetch() } catch (e) { toast.error(e) } }}>
        <Toggle checked={drop} onChange={setDrop} label={<span className="text-rose-600">Xoá luôn dữ liệu (database/key/bucket)</span>} />
      </ConfirmModal>
    </div>
  )
}
