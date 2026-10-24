import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Folder, FileText, Download, Save, ChevronRight, AlertTriangle, ArrowLeft } from 'lucide-react'
import { api } from '../../api'
import { Code, ErrorBox, Loading, Section, Spinner, Toggle } from '../../components/ui'
import { useToast } from '../../components/toast'
import { fmtBytes } from '../../util'
import type { TabProps } from '../AppDetail'

interface Entry { name: string; path: string; dir: boolean; size: number; modified: number }

function Editor({ appId, path, onBack }: { appId: number; path: string; onBack: () => void }) {
  const toast = useToast()
  const q = useQuery({ queryKey: ['app-file', appId, path], queryFn: () => api.get(`/api/apps/${appId}/files?path=${encodeURIComponent(path)}`) })
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (q.data?.content != null) setText(q.data.content) }, [q.data])
  const dirty = q.data && text !== q.data.content
  return (
    <Section title={<span className="flex items-center gap-2"><button className="btn-ghost btn-sm" onClick={onBack}><ArrowLeft className="h-3.5 w-3.5" /></button><span className="font-mono">{path}</span></span>}
      actions={<button className="btn-primary btn-sm" disabled={!dirty || busy} onClick={async () => {
        setBusy(true)
        try { await api.put(`/api/apps/${appId}/files`, { path, content: text }); toast.success('Đã lưu — bấm Deploy để áp dụng'); q.refetch() } catch (e) { toast.error(e) } finally { setBusy(false) }
      }}>{busy ? <Spinner /> : <Save className="h-3.5 w-3.5" />}Lưu</button>}>
      {q.isLoading && <Loading />}
      <ErrorBox error={q.error} />
      {q.data && (
        <div className="p-3">
          {q.data.generated && (
            <div className="mb-2 flex items-center gap-1 rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
              <AlertTriangle className="h-3.5 w-3.5" />File do panel sinh lại mỗi lần deploy — thay đổi ở đây sẽ bị ghi đè.
            </div>
          )}
          {path.split('/').pop() === '.env' && <div className="mb-2 text-xs text-slate-500">Khối dưới dòng "Tekar Hosting: dịch vụ nền tảng" được sinh lại khi deploy.</div>}
          <textarea className="input h-[60vh] font-mono text-xs" spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
        </div>
      )}
    </Section>
  )
}

export default function FilesTab({ app }: TabProps) {
  const [path, setPath] = useState('')
  const [file, setFile] = useState<string | null>(null)
  const [incData, setIncData] = useState(false)
  const [incEnv, setIncEnv] = useState(true)
  const q = useQuery({ queryKey: ['app-files', app.id, path], queryFn: () => api.get(`/api/apps/${app.id}/files?path=${encodeURIComponent(path)}`), enabled: !file })
  const parts = path ? path.split('/') : []
  const exportUrl = `/api/apps/${app.id}/export?include_env=${incEnv}&include_data=${incData}`
  const isOwner = app.my_role === 'owner'

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
        <div>
          <div>Thư mục: <span className="font-mono">{q.data?.root || app.work_dir}</span></div>
          <div className="text-xs text-slate-500">Thư mục compose chuẩn — có thể tự chạy bằng <span className="font-mono">docker compose -p {app.compose_project} up -d</span> mà không cần panel.</div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {isOwner && <Toggle checked={incEnv} onChange={setIncEnv} label=".env" />}
          {(app.volumes_dir || app.data_dir) && <Toggle checked={incData} onChange={setIncData} label="Gồm dữ liệu" />}
          <a className="btn-secondary" href={isOwner ? exportUrl : `/api/apps/${app.id}/export?include_env=false&include_data=${incData}`} download><Download className="h-4 w-4" />Export (.tar.gz)</a>
        </div>
      </div>
      {file ? <Editor appId={app.id} path={file} onBack={() => setFile(null)} /> : (
        <Section title={
          <span className="flex flex-wrap items-center gap-1 font-mono text-xs">
            <button className="text-indigo-600 hover:underline" onClick={() => setPath('')}>{app.slug}</button>
            {parts.map((p, i) => <span key={i} className="flex items-center gap-1"><ChevronRight className="h-3 w-3" /><button className="text-indigo-600 hover:underline" onClick={() => setPath(parts.slice(0, i + 1).join('/'))}>{p}</button></span>)}
          </span>
        }>
          {q.isLoading && <Loading />}
          <ErrorBox error={q.error} />
          {q.data?.entries && (
            <table className="table">
              <thead><tr><th>Tên</th><th>Kích thước</th><th>Sửa đổi</th></tr></thead>
              <tbody>
                {q.data.entries.map((e: Entry) => (
                  <tr key={e.path} className="cursor-pointer" onClick={() => (e.dir ? setPath(e.path) : setFile(e.path))}>
                    <td><span className="flex items-center gap-2">{e.dir ? <Folder className="h-4 w-4 text-amber-500" /> : <FileText className="h-4 w-4 text-slate-400" />}<span className="font-mono text-xs">{e.name}</span></span></td>
                    <td className="text-xs">{e.dir ? '—' : fmtBytes(e.size)}</td>
                    <td className="text-xs text-slate-500">{new Date(e.modified * 1000).toLocaleString('vi-VN', { hour12: false })}</td>
                  </tr>
                ))}
                {!q.data.entries.length && <tr><td colSpan={3} className="text-center text-slate-500">Thư mục trống</td></tr>}
              </tbody>
            </table>
          )}
        </Section>
      )}
      {(app.volumes_dir || app.data_dir) && (
        <div className="text-xs text-slate-500">
          {app.volumes_dir && <div>Dữ liệu volume: <span className="font-mono">{app.volumes_dir}</span></div>}
          {app.data_dir && <div>Data mount (APP_DATA_DIR): <span className="font-mono">{app.data_dir}</span></div>}
        </div>
      )}
      <Code>{`cd ${app.work_dir}\ndocker compose -p ${app.compose_project} up -d`}</Code>
    </div>
  )
}
