import { createContext, useContext, useEffect, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useLocation } from 'react-router-dom'
import { api, setUnauthorizedHandler, type User } from './api'

interface AuthCtx { user: User | null; isLoading: boolean; refresh: () => void; logout: () => Promise<void> }
const Ctx = createContext<AuthCtx>({ user: null, isLoading: true, refresh: () => {}, logout: async () => {} })

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const nav = useNavigate()
  const loc = useLocation()
  const q = useQuery({ queryKey: ['me'], queryFn: () => api.get<User>('/api/auth/me'), retry: false, staleTime: 60000 })

  useEffect(() => {
    setUnauthorizedHandler(() => {
      qc.setQueryData(['me'], null)
      if (!location.pathname.startsWith('/login')) nav('/login', { replace: true })
    })
  }, [qc, nav])

  useEffect(() => {
    if (q.isError && !loc.pathname.startsWith('/login')) nav('/login', { replace: true })
  }, [q.isError, loc.pathname, nav])

  return (
    <Ctx.Provider value={{
      user: q.data ?? null,
      isLoading: q.isLoading,
      refresh: () => qc.invalidateQueries({ queryKey: ['me'] }),
      logout: async () => {
        await api.post('/api/auth/logout').catch(() => {})
        qc.clear()
        nav('/login', { replace: true })
      },
    }}>{children}</Ctx.Provider>
  )
}

export function useAuth() { return useContext(Ctx) }
export function useIsStaff() { const { user } = useAuth(); return user?.role === 'admin' || user?.role === 'operator' }
export function useIsAdmin() { const { user } = useAuth(); return user?.role === 'admin' }
