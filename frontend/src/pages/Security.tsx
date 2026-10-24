import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ShieldCheck, ScanLine } from 'lucide-react'
import { api } from '../api'
import { Empty, ErrorBox, Loading, Modal, PageHeader, Section, SevBadge, SevSummary, Tabs } from '../components/ui'
import JobModal from '../components/JobModal'
import { useToast } from '../components/toast'
import { useIsStaff } from '../auth'
import { fmtDate } from '../util'

const ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO', 'UNKNOWN']

export function FindingsModal({ id, onClose }: { id: number | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ['scan', id], queryFn: () => api.get(`/api/security/results/${id}`), enabled: id != null })
  const [filter, setFilter] = useState('')
  const list = (q.data?.findings || []).filter((f: any) => !filter || f.severity === filter)
  return (
    <Modal open={id != null} onClose={onClose} title={q.data?.target || 'Kết quả quét'} size="xl">
      {q.isLoading && <Loading />}
      {q.data && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-sm"><SevSummary summary={q.data.summary} /><span className="text-xs text-slate-500">{fmtDate(q.data.created_at)}</span>
            <select className="input ml-auto w-auto py-1" value={filter} onChange={(e) => setFilter(e.target.value)}><option value="">Tất cả</option>{ORDER.map((s) => <option key={s}>{s}</option>)}</select>
          </div>
          <table className="table">
            <thead><tr><th>Mức</th><th>ID</th><th>Gói</th><th>Mô tả</th><th>Khắc phục</th></tr></thead>
            <tbody>
              {list.map((f: any, i: number) => (
                <tr key={i}>
                  <td><SevBadge sev={f.severity} /></td>
                  <td className="mono whitespace-nowrap">{f.rule?.startsWith('CVE') || f.rule?.startsWith('GHSA') ? <a className="text-indigo-600" href={`https://avd.aquasec.com/nvd/${f.rule.toLowerCase()}`} target="_blank" rel="noreferrer">{f.rule}</a> : f.rule}</td>
                  <td className="mono break-all">{f.target}</td>
                  <td className="text-xs">{f.title}</td>
                  <td className="text-xs">{f.fix}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!list.length && <Empty>Không có lỗ hổng</Empty>}
        </div>
      )}
    </Modal>
  )
}

export default function Security() {
  const toast = useToast()
  const staff = useIsStaff()
  const [tab, setTab] = useState(staff ? 'config' : 'images')
  const [job, setJob] = useState<number | null>(null)
  const [sel, setSel] = useState<number | null>(null)
  const q = useQuery({ queryKey: ['security-overview'], queryFn: () => api.get('/api/security/overview'), refetchInterval: 30000 })
  const cfg = q.data?.config
  const grouped = useMemo(() => {
    const g: Record<string, any[]> = {}
    ;(cfg?.findings || []).forEach((f: any) => { (g[f.severity] ||= []).push(f) })
    return g
  }, [cfg])
  const scan = async (kind: string) => {
    try { const r = await api.post('/api/security/scan', { kind }); setJob(r.job_ids[r.job_ids.length - 1]) } catch (e) { toast.error(e) }
  }
  const totals = (q.data?.images || []).reduce((a: Record<string, number>, i: any) => { Object.entries(i.summary || {}).forEach(([k, v]) => { a[k] = (a[k] || 0) + (v as number) }); return a }, {})

  return (
    <div>
      <PageHeader title="Bảo mật" subtitle="Kiểm tra cấu hình container/proxy + quét lỗ hổng image bằng Trivy" actions={staff && <>
        <button className="btn-secondary" onClick={() => scan('config')}><ShieldCheck className="h-4 w-4" />Kiểm tra cấu hình</button>
        <button className="btn-primary" onClick={() => scan('images')}><ScanLine className="h-4 w-4" />Quét image (chậm)</button>
      </>} />
      {q.isLoading && <Loading />}
      <ErrorBox error={q.error} />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: 'config', label: <>Cấu hình {cfg && <SevSummary summary={cfg.summary} />}</>, hidden: !staff }, { id: 'images', label: <>Image ({q.data?.images?.length || 0})</> }]} />
      {tab === 'config' && staff && (
        cfg ? (
          <div className="space-y-4">
            <div className="text-xs text-slate-500">Lần kiểm tra: {fmtDate(cfg.created_at)}</div>
            {ORDER.filter((s) => grouped[s]).map((s) => (
              <Section key={s} title={<span className="flex items-center gap-2"><SevBadge sev={s} />{grouped[s].length} vấn đề</span>}>
                <table className="table">
                  <tbody>
                    {grouped[s].map((f: any, i: number) => (
                      <tr key={i}>
                        <td className="w-48 font-medium">{f.target}</td>
                        <td>{f.title}<div className="text-xs text-slate-500">→ {f.fix}</div></td>
                        <td className="mono w-32 text-slate-400">{f.rule}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Section>
            ))}
          </div>
        ) : <div className="card"><Empty>Chưa kiểm tra. Bấm "Kiểm tra cấu hình".</Empty></div>
      )}
      {tab === 'images' && (
        <Section title={<span className="flex items-center gap-2">Tổng: <SevSummary summary={totals} /></span>}>
          <table className="table">
            <thead><tr><th>Image</th><th>Lỗ hổng</th><th>Quét lúc</th></tr></thead>
            <tbody>
              {(q.data?.images || []).slice().sort((a: any, b: any) => (b.summary.CRITICAL || 0) - (a.summary.CRITICAL || 0) || (b.summary.HIGH || 0) - (a.summary.HIGH || 0)).map((i: any) => (
                <tr key={i.id} className="cursor-pointer" onClick={() => setSel(i.id)}>
                  <td className="mono">{i.target}</td><td><SevSummary summary={i.summary} /></td><td className="text-xs text-slate-500">{fmtDate(i.created_at)}</td>
                </tr>
              ))}
              {!q.data?.images?.length && <tr><td colSpan={3} className="text-center text-slate-500">Chưa quét image nào</td></tr>}
            </tbody>
          </table>
        </Section>
      )}
      <FindingsModal id={sel} onClose={() => setSel(null)} />
      <JobModal jobId={job} onClose={() => { setJob(null); q.refetch() }} title="Quét bảo mật" />
    </div>
  )
}
