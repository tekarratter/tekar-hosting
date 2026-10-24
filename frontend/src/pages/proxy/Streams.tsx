import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Pencil, Trash2 } from 'lucide-react'
import { api, type Stream } from '../../api'
import { Badge, ConfirmModal, ErrorBox, Field, Loading, Modal, Section, Spinner, Toggle } from '../../components/ui'
import { useToast } from '../../components/toast'

function StreamForm({ s, onClose, onSaved }: { s: Stream | null; onClose: () => void; onSaved: (r: any) => void }) {
  const toast = useToast()
  const [port, setPort] = useState(s?.listen_port ? String(s.listen_port) : '')
  const [proto, setProto] = useState<Stream['protocol']>(s?.protocol || 'tcp')
  const [up, setUp] = useState(s?.upstream || '')
  const [note, setNote] = useState(s?.note || '')
  const [enabled, setEnabled] = useState(s?.enabled ?? true)
  const [busy, setBusy] = useState(false)
  async function save() {
    setBusy(true)
    try {
      const body = { listen_port: Number(port), protocol: proto, upstream: up, note, enabled }
      const r = s ? await api.put(`/api/proxy/streams/${s.id}`, body) : await api.post('/api/proxy/streams', body)
      toast.success('Đã lưu stream')
      onSaved(r); onClose()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }
  return (
    <Modal open onClose={onClose} title={s ? `Sửa stream :${s.listen_port}` : 'Thêm stream TCP/UDP'} footer={<>
      <button className="btn-secondary" onClick={onClose}>Huỷ</button>
      <button className="btn-primary" disabled={busy || !port || !up} onClick={save}>{busy && <Spinner />}Lưu</button>
    </>}>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Cổng lắng nghe (trên host)"><input className="input" type="number" value={port} onChange={(e) => setPort(e.target.value)} placeholder="5432" /></Field>
        <Field label="Giao thức">
          <select className="input" value={proto} onChange={(e) => setProto(e.target.value as Stream['protocol'])}>
            <option value="tcp">TCP</option><option value="udp">UDP</option><option value="both">TCP + UDP</option>
          </select>
        </Field>
        <Field label="Đích" hint="container:port · host:port · IP:port" className="md:col-span-2"><input className="input font-mono" value={up} onChange={(e) => setUp(e.target.value)} placeholder="db-1:5432" /></Field>
        <Field label="Ghi chú" className="md:col-span-2"><input className="input" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <Toggle checked={enabled} onChange={setEnabled} label="Bật" />
      </div>
      <div className="mt-3 text-xs text-slate-500">Đổi cổng sẽ tạo lại container th-caddy để publish cổng mới (gián đoạn vài giây).</div>
    </Modal>
  )
}

export default function Streams({ onJob }: { onJob: (id: number, title: string) => void }) {
  const toast = useToast()
  const q = useQuery({ queryKey: ['streams'], queryFn: () => api.get<Stream[]>('/api/proxy/streams') })
  const [edit, setEdit] = useState<Stream | null | undefined>(undefined)
  const [del, setDel] = useState<Stream | null>(null)
  const done = (r: any) => { q.refetch(); if (r?.job_id) onJob(r.job_id, 'Publish cổng stream') }
  return (
    <Section title="Streams (chuyển tiếp TCP/UDP)" actions={<button className="btn-primary btn-sm" onClick={() => setEdit(null)}><Plus className="h-3.5 w-3.5" />Thêm stream</button>}>
      {q.isLoading && <Loading />}
      <ErrorBox error={q.error} />
      <table className="table">
        <thead><tr><th>Cổng</th><th>Giao thức</th><th>Đích</th><th>Ghi chú</th><th>Trạng thái</th><th></th></tr></thead>
        <tbody>
          {(q.data || []).map((s) => (
            <tr key={s.id} className={s.enabled ? '' : 'opacity-50'}>
              <td className="font-mono font-medium">:{s.listen_port}</td>
              <td><Badge color="blue">{s.protocol === 'both' ? 'TCP+UDP' : s.protocol.toUpperCase()}</Badge></td>
              <td className="mono">{s.upstream}</td>
              <td className="text-xs">{s.note}</td>
              <td>{s.enabled ? <Badge color="green">bật</Badge> : <Badge>tắt</Badge>}</td>
              <td className="whitespace-nowrap text-right">
                <button className="btn-ghost btn-sm" onClick={() => setEdit(s)}><Pencil className="h-3.5 w-3.5" /></button>
                <button className="btn-ghost btn-sm" onClick={() => setDel(s)}><Trash2 className="h-3.5 w-3.5" /></button>
              </td>
            </tr>
          ))}
          {q.data && !q.data.length && <tr><td colSpan={6} className="text-center text-slate-500">Chưa có stream</td></tr>}
        </tbody>
      </table>
      {edit !== undefined && <StreamForm s={edit} onClose={() => setEdit(undefined)} onSaved={done} />}
      <ConfirmModal open={!!del} onClose={() => setDel(null)} title="Xoá stream" message={<>Xoá stream cổng <b>{del?.listen_port}</b>?</>}
        onConfirm={async () => { try { const r = await api.del(`/api/proxy/streams/${del!.id}`); toast.success('Đã xoá'); setDel(null); done(r) } catch (e) { toast.error(e) } }} />
    </Section>
  )
}
