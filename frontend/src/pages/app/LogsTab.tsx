import { useState } from 'react'
import LogStream from '../../components/LogStream'
import { Empty } from '../../components/ui'
import type { TabProps } from '../AppDetail'

export default function LogsTab({ app }: TabProps) {
  const cs = (app.containers || []) as any[]
  const [sel, setSel] = useState<string>(cs[0]?.name || '')
  if (!cs.length) return <Empty>Không có container</Empty>
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1">
        {cs.map((c) => (
          <button key={c.name} className={sel === c.name ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'} onClick={() => setSel(c.name)}>{c.service || c.name}</button>
        ))}
      </div>
      {sel && <LogStream key={sel} container={sel} height={560} />}
    </div>
  )
}
