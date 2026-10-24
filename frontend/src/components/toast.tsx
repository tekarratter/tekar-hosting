import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { CheckCircle2, AlertTriangle, Info, X } from 'lucide-react'

type Kind = 'success' | 'error' | 'info'
interface Toast { id: number; kind: Kind; text: string }
const Ctx = createContext<(kind: Kind, text: string) => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([])
  const push = useCallback((kind: Kind, text: string) => {
    const id = Date.now() + Math.random()
    setItems((x) => [...x, { id, kind, text }])
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), kind === 'error' ? 8000 : 4000)
  }, [])
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} className={`card flex items-start gap-2 p-3 text-sm shadow-lg ${t.kind === 'error' ? 'border-rose-300 dark:border-rose-800' : ''}`}>
            {t.kind === 'success' && <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />}
            {t.kind === 'error' && <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-500" />}
            {t.kind === 'info' && <Info className="mt-0.5 h-4 w-4 shrink-0 text-indigo-500" />}
            <div className="flex-1 whitespace-pre-wrap break-words">{t.text}</div>
            <button onClick={() => setItems((x) => x.filter((i) => i.id !== t.id))}><X className="h-4 w-4 text-slate-400" /></button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  )
}

export function useToast() {
  const push = useContext(Ctx)
  return {
    success: (t: string) => push('success', t),
    error: (e: unknown) => push('error', e instanceof Error ? e.message : String(e)),
    info: (t: string) => push('info', t),
  }
}
