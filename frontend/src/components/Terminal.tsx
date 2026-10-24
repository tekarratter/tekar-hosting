import { useEffect, useRef, useState } from 'react'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { RotateCw } from 'lucide-react'
import { wsUrl } from '../api'

export default function Terminal({ container, shell = 'auto', height = 480, run }: { container: string; shell?: string; height?: number; run?: string }) {
  const el = useRef<HTMLDivElement>(null)
  const [nonce, setNonce] = useState(0)
  const [status, setStatus] = useState('connecting')

  useEffect(() => {
    if (!el.current) return
    const term = new XTerm({
      cursorBlink: true, fontSize: 13, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      theme: { background: '#0b1020', foreground: '#e2e8f0' }, scrollback: 5000, convertEol: false,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(el.current)
    try { fit.fit() } catch { /* ignore */ }
    const ws = new WebSocket(wsUrl(`/api/ws/terminal/${encodeURIComponent(container)}?shell=${encodeURIComponent(shell)}${run ? `&run=${encodeURIComponent(run)}` : ''}`))
    ws.binaryType = 'arraybuffer'
    const sendResize = () => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
    }
    ws.onopen = () => { setStatus('open'); sendResize(); term.focus() }
    ws.onmessage = (e) => {
      if (typeof e.data === 'string') term.write(e.data)
      else term.write(new Uint8Array(e.data as ArrayBuffer))
    }
    ws.onclose = () => setStatus('closed')
    const sub = term.onData((d) => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'input', data: d })) })
    const ro = new ResizeObserver(() => { try { fit.fit(); sendResize() } catch { /* ignore */ } })
    ro.observe(el.current)
    return () => { ro.disconnect(); sub.dispose(); ws.close(); term.dispose() }
  }, [container, shell, run, nonce])

  return (
    <div className="overflow-hidden rounded-md border border-slate-800 bg-[#0b1020]">
      <div className="flex items-center justify-between border-b border-slate-800 px-3 py-1.5 text-xs text-slate-400">
        <span className="font-mono">{container}</span>
        <span className="flex items-center gap-2">
          <span className={status === 'open' ? 'text-emerald-400' : status === 'closed' ? 'text-rose-400' : 'text-amber-400'}>●</span>{status}
          <button className="rounded px-1.5 py-0.5 hover:bg-slate-800" onClick={() => setNonce((n) => n + 1)} title="Kết nối lại"><RotateCw className="h-3.5 w-3.5" /></button>
        </span>
      </div>
      <div ref={el} style={{ height }} className="p-1" />
    </div>
  )
}
