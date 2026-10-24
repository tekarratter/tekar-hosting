import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Job } from '../api'
import { Modal, StatusBadge, Spinner } from './ui'
import { fmtDate } from '../util'

export function JobLog({ jobId, onFinish }: { jobId: number; onFinish?: (j: Job) => void }) {
  const qc = useQueryClient()
  const ref = useRef<HTMLPreElement>(null)
  const done = useRef(false)
  const q = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.get<Job>(`/api/jobs/${jobId}`),
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === 'success' || s === 'failed' ? false : 1500
    },
  })
  const j = q.data
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [j?.log])
  useEffect(() => {
    if (j && (j.status === 'success' || j.status === 'failed') && !done.current) {
      done.current = true
      onFinish?.(j)
      qc.invalidateQueries()
    }
  }, [j, onFinish, qc])
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-mono">#{jobId}</span>
        <span className="font-medium">{j?.kind}</span>
        <StatusBadge status={j?.status} />
        {j && j.status !== 'success' && j.status !== 'failed' && <Spinner />}
        <span className="text-xs text-slate-500">{fmtDate(j?.created_at)}</span>
      </div>
      <pre ref={ref} className="h-96 overflow-auto whitespace-pre-wrap break-all rounded-md bg-slate-950 p-3 font-mono text-xs text-slate-200">
        {j?.log || (j ? 'Đang chờ worker…' : 'Đang tải…')}
      </pre>
      {j?.result && Object.keys(j.result).length > 0 && (
        <div className="text-xs text-slate-500">Kết quả: <span className="font-mono">{JSON.stringify(j.result)}</span></div>
      )}
    </div>
  )
}

export default function JobModal({ jobId, onClose, title = 'Tiến trình' }: { jobId: number | null; onClose: () => void; title?: string }) {
  return (
    <Modal open={jobId != null} onClose={onClose} title={title} size="lg" footer={<button className="btn-secondary" onClick={onClose}>Đóng</button>}>
      {jobId != null && <JobLog jobId={jobId} />}
    </Modal>
  )
}
