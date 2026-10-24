import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, ArrowUp, ArrowDown, Lock } from 'lucide-react'
import { api, type AccessList } from '../../api'
import { Badge, ConfirmModal, ErrorBox, Field, Loading, Modal, Section, Spinner, Toggle } from '../../components/ui'
import { useToast } from '../../components/toast'

function AclForm({ acl, onClose, onSaved }: { acl: AccessList | null; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const [name, setName] = useState(acl?.name || '')
  const [any, setAny] = useState(acl?.satisfy_any ?? false)
  const [pass, setPass] = useState(acl?.pass_auth ?? false)
  const [users, setUsers] = useState<{ username: string; password: string }[]>((acl?.auth || []).map((u) => ({ username: u.username, password: '' })))
  const [rules, setRules] = useState<{ address: string; directive: 'allow' | 'deny' }[]>(acl?.clients || [])
  const [busy, setBusy] = useState(false)
  const move = (i: number, d: number) => {
    const j = i + d
    if (j < 0 || j >= rules.length) return
    const n = [...rules];[n[i], n[j]] = [n[j], n[i]]; setRules(n)
  }
  async function save() {
    setBusy(true)
    try {
      const body = { name, satisfy_any: any, pass_auth: pass, auth: users.filter((u) => u.username).map((u) => (u.password ? u : { username: u.username })), clients: rules.filter((r) => r.address) }
      const r = acl ? await api.put(`/api/proxy/access-lists/${acl.id}`, body) : await api.post('/api/proxy/access-lists', body)
      if (r.apply?.caddy && r.apply.caddy !== 'ok') toast.error(`Đã lưu nhưng Caddy: ${r.apply.caddy}`)
      else toast.success('Đã lưu access list')
      onSaved(); onClose()
    } catch (e) { toast.error(e) } finally { setBusy(false) }
  }
  return (
    <Modal open onClose={onClose} title={acl ? `Sửa access list: ${acl.name}` : 'Tạo access list'} size="lg" footer={<>
      <button className="btn-secondary" onClick={onClose}>Huỷ</button>
      <button className="btn-primary" disabled={busy || !name} onClick={save}>{busy && <Spinner />}Lưu</button>
    </>}>
      <div className="space-y-4">
        <Field label="Tên"><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            <button type="button" className={!any ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'} onClick={() => setAny(false)}>Phải đạt cả 2 (IP và đăng nhập)</button>
            <button type="button" className={any ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'} onClick={() => setAny(true)}>Đạt 1 trong 2 (IP hoặc đăng nhập)</button>
          </div>
          <Toggle checked={pass} onChange={setPass} label="Chuyển header Authorization xuống upstream" />
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className="label">Tài khoản Basic Auth</label>
            <button className="btn-secondary btn-sm" onClick={() => setUsers([...users, { username: '', password: '' }])}><Plus className="h-3.5 w-3.5" />Thêm</button>
          </div>
          {users.map((u, i) => (
            <div key={i} className="mb-1 flex gap-1">
              <input className="input" placeholder="user" value={u.username} onChange={(e) => setUsers(users.map((x, j) => j === i ? { ...x, username: e.target.value } : x))} />
              <input className="input" type="password" placeholder={acl?.auth.some((a) => a.username === u.username) ? '(để trống = giữ nguyên)' : 'mật khẩu'} value={u.password} onChange={(e) => setUsers(users.map((x, j) => j === i ? { ...x, password: e.target.value } : x))} />
              <button className="btn-ghost btn-sm" onClick={() => setUsers(users.filter((_, j) => j !== i))}><Trash2 className="h-3.5 w-3.5" /></button>
            </div>
          ))}
          {!users.length && <div className="text-xs text-slate-500">Không có tài khoản — chỉ lọc theo IP.</div>}
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className="label">Rule IP (theo thứ tự — rule khớp đầu tiên quyết định; không khớp rule nào = chặn)</label>
            <button className="btn-secondary btn-sm" onClick={() => setRules([...rules, { address: '', directive: 'allow' }])}><Plus className="h-3.5 w-3.5" />Thêm</button>
          </div>
          {rules.map((r, i) => (
            <div key={i} className="mb-1 flex items-center gap-1">
              <select className="input w-28" value={r.directive} onChange={(e) => setRules(rules.map((x, j) => j === i ? { ...x, directive: e.target.value as 'allow' | 'deny' } : x))}>
                <option value="allow">allow</option><option value="deny">deny</option>
              </select>
              <input className="input flex-1 font-mono" placeholder="1.2.3.0/24 hoặc all" value={r.address} onChange={(e) => setRules(rules.map((x, j) => j === i ? { ...x, address: e.target.value } : x))} />
              <button className="btn-ghost btn-sm" onClick={() => move(i, -1)}><ArrowUp className="h-3.5 w-3.5" /></button>
              <button className="btn-ghost btn-sm" onClick={() => move(i, 1)}><ArrowDown className="h-3.5 w-3.5" /></button>
              <button className="btn-ghost btn-sm" onClick={() => setRules(rules.filter((_, j) => j !== i))}><Trash2 className="h-3.5 w-3.5" /></button>
            </div>
          ))}
          {!rules.length && <div className="text-xs text-slate-500">Không có rule IP — {any ? 'mọi IP đều phải đăng nhập' : 'không lọc IP'}.</div>}
          <div className="mt-1 text-xs text-slate-500">IP thật lấy từ CF-Connecting-IP khi đi qua Cloudflare Tunnel.</div>
        </div>
      </div>
    </Modal>
  )
}

export default function AccessLists({ canEdit }: { canEdit: boolean }) {
  const toast = useToast()
  const q = useQuery({ queryKey: ['access-lists'], queryFn: () => api.get<AccessList[]>('/api/proxy/access-lists') })
  const [edit, setEdit] = useState<AccessList | null | undefined>(undefined)
  const [del, setDel] = useState<AccessList | null>(null)
  return (
    <Section title="Access lists" actions={canEdit && <button className="btn-primary btn-sm" onClick={() => setEdit(null)}><Plus className="h-3.5 w-3.5" />Tạo access list</button>}>
      {q.isLoading && <Loading />}
      <ErrorBox error={q.error} />
      <table className="table">
        <thead><tr><th>Tên</th><th>Điều kiện</th><th>Tài khoản</th><th>Rule IP</th><th>Dùng bởi</th><th></th></tr></thead>
        <tbody>
          {(q.data || []).map((a) => (
            <tr key={a.id}>
              <td className="font-medium"><Lock className="mr-1 inline h-3.5 w-3.5 text-slate-400" />{a.name}</td>
              <td className="text-xs">{a.satisfy_any ? 'IP hoặc đăng nhập' : 'IP và đăng nhập'}{a.pass_auth && <Badge color="gray">pass auth</Badge>}</td>
              <td className="text-xs">{a.auth.map((u) => u.username).join(', ') || '—'}</td>
              <td className="mono">{a.clients.map((c, i) => <div key={i}><span className={c.directive === 'allow' ? 'text-emerald-600' : 'text-rose-600'}>{c.directive}</span> {c.address}</div>)}{!a.clients.length && '—'}</td>
              <td>{a.used_by} host</td>
              <td className="whitespace-nowrap text-right">{canEdit && <>
                <button className="btn-ghost btn-sm" onClick={() => setEdit(a)}><Pencil className="h-3.5 w-3.5" /></button>
                <button className="btn-ghost btn-sm" onClick={() => setDel(a)}><Trash2 className="h-3.5 w-3.5" /></button>
              </>}</td>
            </tr>
          ))}
          {q.data && !q.data.length && <tr><td colSpan={6} className="text-center text-slate-500">Chưa có access list</td></tr>}
        </tbody>
      </table>
      {edit !== undefined && <AclForm acl={edit} onClose={() => setEdit(undefined)} onSaved={() => q.refetch()} />}
      <ConfirmModal open={!!del} onClose={() => setDel(null)} title="Xoá access list" message={<>Xoá <b>{del?.name}</b>? {del?.used_by ? `${del.used_by} host đang dùng sẽ trở thành công khai.` : ''}</>}
        onConfirm={async () => { try { await api.del(`/api/proxy/access-lists/${del!.id}`); toast.success('Đã xoá'); setDel(null); q.refetch() } catch (e) { toast.error(e) } }} />
    </Section>
  )
}
