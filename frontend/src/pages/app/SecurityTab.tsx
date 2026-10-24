import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ShieldCheck } from 'lucide-react'
import { api } from '../../api'
import { Section, SevSummary } from '../../components/ui'
import { FindingsModal } from '../Security'
import { fmtDate } from '../../util'
import type { TabProps } from '../AppDetail'

export default function SecurityTab({ app, runJob, canDeploy }: TabProps) {
  const q = useQuery({ queryKey: ['security-overview'], queryFn: () => api.get('/api/security/overview') })
  const images = new Set(((app.containers || []) as any[]).map((c) => c.image))
  const rows = (q.data?.images || []).filter((i: any) => i.app_id === app.id || images.has(i.target))
  const findings = (q.data?.config?.findings || []).filter((f: any) => ((app.containers || []) as any[]).some((c) => c.name === f.target))
  const [sel, setSel] = useState<number | null>(null)
  return (
    <div className="space-y-4">
      <Section title="Lỗ hổng image (Trivy)" actions={canDeploy && <button className="btn-primary btn-sm" onClick={() => runJob(api.post(`/api/security/apps/${app.id}/scan`), 'Quét image')}><ShieldCheck className="h-3.5 w-3.5" />Quét ngay</button>}>
        <table className="table">
          <thead><tr><th>Image</th><th>Kết quả</th><th>Thời gian</th></tr></thead>
          <tbody>
            {rows.map((r: any) => (
              <tr key={r.id} className="cursor-pointer" onClick={() => setSel(r.id)}><td className="mono">{r.target}</td><td><SevSummary summary={r.summary} /></td><td className="text-xs text-slate-500">{fmtDate(r.created_at)}</td></tr>
            ))}
            {!rows.length && <tr><td colSpan={3} className="text-center text-slate-500">Chưa quét</td></tr>}
          </tbody>
        </table>
      </Section>
      {findings.length > 0 && (
        <Section title="Vấn đề cấu hình">
          <table className="table">
            <tbody>{findings.map((f: any, i: number) => <tr key={i}><td><SevSummary summary={{ [f.severity]: 1 }} /></td><td>{f.target}</td><td>{f.title}<div className="text-xs text-slate-500">{f.fix}</div></td></tr>)}</tbody>
          </table>
        </Section>
      )}
      <FindingsModal id={sel} onClose={() => setSel(null)} />
    </div>
  )
}
