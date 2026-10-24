import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api, type Job } from '../api'
import { ErrorBox, Loading, PageHeader, Section, StatusBadge } from '../components/ui'
import { JobLog } from '../components/JobModal'
import { cls, fmtDate } from '../util'

function dur(j: Job) {
  if (!j.started_at) return '—'
  const s = ((j.finished_at ? new Date(j.finished_at) : new Date()).getTime() - new Date(j.started_at).getTime()) / 1000
  return s < 60 ? `${s.toFixed(0)}s` : `${(s / 60).toFixed(1)}m`
}

export default function Jobs() {
  const { id } = useParams()
  const nav = useNavigate()
  const q = useQuery({ queryKey: ['jobs'], queryFn: () => api.get<Job[]>('/api/jobs?limit=100'), refetchInterval: 5000 })
  return (
    <div>
      <PageHeader title="Jobs" subtitle="Tác vụ nền: deploy, backup, quét, báo cáo…" />
      <ErrorBox error={q.error} />
      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <div className="card overflow-x-auto">
          {q.isLoading ? <Loading /> : (
            <table className="table">
              <thead><tr><th>#</th><th>Loại</th><th>Trạng thái</th><th>Tạo lúc</th><th>Thời lượng</th></tr></thead>
              <tbody>
                {(q.data || []).map((j) => (
                  <tr key={j.id} className={cls('cursor-pointer', String(j.id) === id && 'bg-indigo-50 dark:bg-indigo-950/40')} onClick={() => nav(`/jobs/${j.id}`)}>
                    <td className="font-mono text-xs">{j.id}</td>
                    <td>{j.kind}{j.result?.error && <div className="max-w-xs truncate text-xs text-rose-600" title={j.result.error}>{j.result.error}</div>}</td>
                    <td><StatusBadge status={j.status} /></td>
                    <td className="whitespace-nowrap text-xs">{fmtDate(j.created_at)}</td>
                    <td className="text-xs">{dur(j)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <Section title={id ? `Job #${id}` : 'Chọn một job để xem log'}>
          <div className="p-3">{id && <JobLog key={id} jobId={Number(id)} />}</div>
        </Section>
      </div>
    </div>
  )
}
