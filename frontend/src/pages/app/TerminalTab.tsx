import { useState } from 'react'
import Terminal from '../../components/Terminal'
import { Empty } from '../../components/ui'
import type { TabProps } from '../AppDetail'

export default function TerminalTab({ app }: TabProps) {
  const cs = ((app.containers || []) as any[]).filter((c) => c.state === 'running')
  const [sel, setSel] = useState<string>(cs[0]?.name || '')
  const [shell, setShell] = useState('auto')
  if (!cs.length) return <Empty>Không có container đang chạy</Empty>
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <select className="input w-auto" value={sel} onChange={(e) => setSel(e.target.value)}>
          {cs.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
        </select>
        <select className="input w-auto" value={shell} onChange={(e) => setShell(e.target.value)}>
          <option value="auto">auto (bash → sh)</option><option value="/bin/sh">/bin/sh</option><option value="/bin/bash">/bin/bash</option>
        </select>
        <span className="text-xs text-slate-500">Mọi phiên terminal đều được ghi audit log.</span>
      </div>
      {sel && <Terminal key={sel + shell} container={sel} shell={shell} height={560} />}
    </div>
  )
}
