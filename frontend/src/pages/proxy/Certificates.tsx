import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Upload, Trash2, RefreshCw } from 'lucide-react'
import { api, type CustomCert, type ManagedCert } from '../../api'
import { Badge, ConfirmModal, ErrorBox, Field, Loading, Modal, Section, Spinner } from '../../components/ui'
import { useToast } from '../../components/toast'
import { fmtDate } from '../../util'

function Days({ d }: { d: number | null }) {
  if (d == null) return <span>—</span>
  return <Badge color={d < 7 ? 'red' : d < 30 ? 'yellow' : 'green'}>{d} ngày</Badge>
}

function PemInput({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <Field label={label}>
      <textarea className="input font-mono text-xs" rows={6} value={value} onChange={(e) => onChange(e.target.value)} placeholder="-----BEGIN ...-----" />
      <input type="file" className="mt-1 text-xs" accept=".pem,.crt,.key,.cer,.txt" onChange={async (e) => { const f = e.target.files?.[0]; if (f) onChange(await f.text()) }} />
    </Field>
  )
}

function CertForm({ cert, onClose, onSaved }: { cert: CustomCert | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [name, setName] = useState(cert?.name || '')
  const [crt, setCrt] = useState('')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  async function save() {
    setBusy(true)
    try {
      const body = { name, cert_pem: crt, key_pem: key }
      const r = cert ? await api.put(`/api/proxy/certificates/${cert.id}`, body) : await api.post('/api/proxy/certificates', body)
      toast.success(`Đã lưu chứng chỉ ${r.domains?.join(', ') || ''}`)
      onSaved(); onClose()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }
  return (
    <Modal open onClose={onClose} title={cert ? `Thay chứng chỉ: ${cert.name}` : 'Tải lên chứng chỉ'} size="lg" footer={<>
      <button className="btn-secondary" onClick={onClose}>Huỷ</button>
      <button className="btn-primary" disabled={busy || !name || !crt || !key} onClick={save}>{busy && <Spinner />}Lưu</button>
    </>}>
      <div className="space-y-3">
        <Field label="Tên"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <PemInput label="Chứng chỉ (fullchain PEM)" value={crt} onChange={setCrt} />
        <PemInput label="Private key (PEM, không mật khẩu)" value={key} onChange={setKey} />
        <div className="text-xs text-slate-500">Chứng chỉ tải lên không tự gia hạn — panel cảnh báo khi còn ≤14 ngày.</div>
      </div>
    </Modal>
  )
}

export default function Certificates({ canEdit }: { canEdit: boolean }) {
  const toast = useToast()
  const q = useQuery({ queryKey: ['certificates'], queryFn: () => api.get<{ custom: CustomCert[]; managed: ManagedCert[] }>('/api/proxy/certificates') })
  const [edit, setEdit] = useState<CustomCert | null | undefined>(undefined)
  const [del, setDel] = useState<CustomCert | null>(null)
  return (
    <div className="space-y-4">
      {q.isLoading && <Loading />}
      <ErrorBox error={q.error} />
      <Section title="Chứng chỉ tự động (Let's Encrypt / ZeroSSL / internal — Caddy tự gia hạn)" actions={<button className="btn-ghost btn-sm" onClick={() => q.refetch()}><RefreshCw className="h-3.5 w-3.5" /></button>}>
        <table className="table">
          <thead><tr><th>Domain</th><th>Issuer</th><th>Hết hạn</th><th>Còn</th></tr></thead>
          <tbody>
            {(q.data?.managed || []).map((c) => (
              <tr key={c.name + c.issuer}><td className="mono">{c.name}</td><td className="text-xs">{c.issuer}</td><td className="text-xs">{fmtDate(c.not_after)}</td><td><Days d={c.days_left} /></td></tr>
            ))}
            {q.data && !q.data.managed.length && <tr><td colSpan={4} className="text-center text-slate-500">Chưa có (route qua Cloudflare Tunnel không cần cert tại Caddy)</td></tr>}
          </tbody>
        </table>
      </Section>
      <Section title="Chứng chỉ tải lên" actions={canEdit && <button className="btn-primary btn-sm" onClick={() => setEdit(null)}><Upload className="h-3.5 w-3.5" />Tải lên</button>}>
        <table className="table">
          <thead><tr><th>Tên</th><th>Domain</th><th>Issuer</th><th>Hết hạn</th><th>Dùng bởi</th><th></th></tr></thead>
          <tbody>
            {(q.data?.custom || []).map((c) => (
              <tr key={c.id}>
                <td className="font-medium">{c.name}</td>
                <td className="mono">{c.domains.join(', ')}</td>
                <td className="text-xs">{c.issuer}</td>
                <td className="text-xs">{fmtDate(c.not_after)} <Days d={c.days_left} /></td>
                <td>{c.used_by} host</td>
                <td className="whitespace-nowrap text-right">{canEdit && <>
                  <button className="btn-ghost btn-sm" title="Thay" onClick={() => setEdit(c)}><RefreshCw className="h-3.5 w-3.5" /></button>
                  <button className="btn-ghost btn-sm" onClick={() => setDel(c)}><Trash2 className="h-3.5 w-3.5" /></button>
                </>}</td>
              </tr>
            ))}
            {q.data && !q.data.custom.length && <tr><td colSpan={6} className="text-center text-slate-500">Chưa có chứng chỉ tải lên</td></tr>}
          </tbody>
        </table>
      </Section>
      {edit !== undefined && <CertForm cert={edit} onClose={() => setEdit(undefined)} onSaved={() => q.refetch()} />}
      <ConfirmModal open={!!del} onClose={() => setDel(null)} title="Xoá chứng chỉ" message={<>Xoá <b>{del?.name}</b>? Host đang dùng sẽ quay về ACME/không TLS.</>}
        onConfirm={async () => { try { await api.del(`/api/proxy/certificates/${del!.id}`); toast.success('Đã xoá'); setDel(null); q.refetch() } catch (e) { toast.error(e) } }} />
    </div>
  )
}
