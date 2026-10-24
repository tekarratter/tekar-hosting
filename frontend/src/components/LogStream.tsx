import { useEffect, useRef, useState } from 'react'
import { Pause, Play, Trash2, Download } from 'lucide-react'
import { wsUrl } from '../api'

const MAX = 4000

export default function LogStream({ container, height = 480 }: { container: string; height?: number }) {
  const [lines, setLines] = useState<string[]>([])
  const [paused, setPaused] = useState(false)
  const [filter, setFilter] = useState('')
  const [status, setStatus] = useState('connecting')
  const ref = useRef<HTMLPreElement>(null)
  const pausedRef = useRef(false)
  pausedRef.current = paused

  useEffect(() => {
    setLines([])
    const ws = new WebSocket(wsUrl(`/api/ws/logs/${encodeURIComponent(container)}?tail=300`))
    let buf = ''
    ws.onopen = () => setStatus('live')
    ws.onclose = () => setStatus('closed')
    ws.onmessage = (e) => {
      buf += String(e.data)
      const parts = buf.split('\n')
      buf = parts.pop() || ''
      if (parts.length) setLines((l) => { const n = l.concat(parts); return n.length > MAX ? n.slice(n.length - MAX) : n })
    }
    return () => ws.close()
  }, [container])

  useEffect(() => {
    if (!pausedRef.current && ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [lines])

  const shown = filter ? lines.filter((l) => l.toLowerCase().includes(filter.toLowerCase())) : lines
  return (
    <div className="overflow-hidden rounded-md border border-slate-800 bg-slate-950">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-3 py-1.5 text-xs text-slate-400">
        <span className="font-mono">{container}</span>
        <span className={status === 'live' ? 'text-emerald-400' : 'text-rose-400'}>● {status}</span>
        <input className="ml-auto w-48 rounded bg-slate-900 px-2 py-0.5 text-slate-200 outline-none" placeholder="Lọc…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <button className="rounded px-1.5 py-0.5 hover:bg-slate-800" onClick={() => setPaused(!paused)} title={paused ? 'Tiếp tục cuộn' : 'Dừng cuộn'}>{paused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}</button>
        <button className="rounded px-1.5 py-0.5 hover:bg-slate-800" onClick={() => setLines([])} title="Xoá"><Trash2 className="h-3.5 w-3.5" /></button>
        <button className="rounded px-1.5 py-0.5 hover:bg-slate-800" title="Tải về" onClick={() => {
          const blob = new Blob([lines.join('\n')], { type: 'text/plain' })
          const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `${container}.log`; a.click()
        }}><Download className="h-3.5 w-3.5" /></button>
      </div>
      <pre ref={ref} style={{ height }} className="overflow-auto whitespace-pre-wrap break-all p-3 font-mono text-[11.5px] leading-relaxed text-slate-200">
        {shown.map((l, i) => {
          const sp = l.indexOf(' ')
          const ts = sp > 0 && /^\d{4}-\d\d-\d\dT/.test(l) ? l.slice(0, sp) : ''
          const body = ts ? l.slice(sp + 1) : l
          const err = /error|exception|fatal|panic|traceback/i.test(body)
          const warn = !err && /warn/i.test(body)
          return (
            <div key={i} className={err ? 'text-rose-300' : warn ? 'text-amber-200' : ''}>
              {ts && <span className="mr-2 text-slate-500">{ts.slice(11, 19)}</span>}{body}
            </div>
          )
        })}
      </pre>
    </div>
  )
}
