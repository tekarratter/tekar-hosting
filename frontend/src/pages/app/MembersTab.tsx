import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { api } from '../../api'
import { Badge, Section } from '../../components/ui'
import { useToast } from '../../components/toast'
import type { TabProps } from '../AppDetail'

const ROLES = [['viewer', 'Xem (status, logs, metrics)'], ['deployer', 'Triển khai (deploy, env, terminal, backup)'], ['owner', 'Chủ (toàn quyền, thành viên, xoá)']]

export default function MembersTab({ app, isOwner }: TabProps) {
  const toast = useToast()
  const q = useQuery({ queryKey: ['members', app.id], queryFn: () => api.get(`/api/apps/${app.id}/members`) })
  const users = useQuery({ queryKey: ['users'], queryFn: () => api.get<any[]>('/api/users'), enabled: isOwner })
  const [uid, setUid] = useState<number | ''>('')
  const [role, setRole] = useState('viewer')
  async function add() {
    try { await api.post(`/api/apps/${app.id}/members`, { user_id: uid, role }); toast.success('Đã cấp quyền'); setUid(''); q.refetch() } catch (e) { toast.error(e) }
  }
  return (
    <div className="space-y-4">
      <Section title="Thành viên">
        <table className="table">
          <thead><tr><th>Người dùng</th><th>Quyền</th><th></th></tr></thead>
          <tbody>
            {q.data?.owner && <tr><td>{q.data.owner.name || q.data.owner.email}<div className="text-xs text-slate-500">{q.data.owner.email}</div></td><td><Badge color="purple">owner (chủ sở hữu)</Badge></td><td /></tr>}
            {(q.data?.members || []).map((m: any) => (
              <tr key={m.id}>
                <td>{m.name || m.email}<div className="text-xs text-slate-500">{m.email}</div></td>
                <td>
                  {isOwner ? (
                    <select className="input w-auto py-1" value={m.role} onChange={async (e) => { try { await api.post(`/api/apps/${app.id}/members`, { user_id: m.user_id, role: e.target.value }); q.refetch() } catch (er) { toast.error(er) } }}>
                      {ROLES.map(([r]) => <option key={r}>{r}</option>)}
                    </select>
                  ) : <Badge>{m.role}</Badge>}
                </td>
                <td className="text-right">{isOwner && <button className="btn-ghost btn-sm" onClick={async () => { await api.del(`/api/apps/${app.id}/members/${m.id}`); q.refetch() }}><Trash2 className="h-3.5 w-3.5" /></button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="border-t border-slate-200 p-3 text-xs text-slate-500 dark:border-slate-800">Admin/operator luôn có toàn quyền trên mọi app.</div>
      </Section>
      {isOwner && (
        <Section title="Cấp quyền">
          <div className="flex flex-wrap items-end gap-2 p-4">
            <select className="input w-64" value={uid} onChange={(e) => setUid(e.target.value ? Number(e.target.value) : '')}>
              <option value="">— chọn người dùng —</option>
              {(users.data || []).filter((u) => u.role === 'member').map((u) => <option key={u.id} value={u.id}>{u.name || u.email} ({u.email})</option>)}
            </select>
            <select className="input w-auto" value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLES.map(([r, l]) => <option key={r} value={r}>{l}</option>)}
            </select>
            <button className="btn-primary" disabled={!uid} onClick={add}>Thêm</button>
          </div>
        </Section>
      )}
    </div>
  )
}
