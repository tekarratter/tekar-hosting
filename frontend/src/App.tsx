import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth'
import Layout from './components/Layout'
import { Loading } from './components/ui'
import Login from './pages/Login'

const Dashboard = lazy(() => import('./pages/Dashboard'))
const Apps = lazy(() => import('./pages/Apps'))
const AppDetail = lazy(() => import('./pages/AppDetail'))
const Stacks = lazy(() => import('./pages/Stacks'))
const Containers = lazy(() => import('./pages/Containers'))
const Platform = lazy(() => import('./pages/Platform'))
const Proxy = lazy(() => import('./pages/Proxy'))
const Cloudflare = lazy(() => import('./pages/Cloudflare'))
const Backups = lazy(() => import('./pages/Backups'))
const Storage = lazy(() => import('./pages/Storage'))
const Security = lazy(() => import('./pages/Security'))
const Claude = lazy(() => import('./pages/Claude'))
const Jobs = lazy(() => import('./pages/Jobs'))
const Alerts = lazy(() => import('./pages/Alerts'))
const Audit = lazy(() => import('./pages/Audit'))
const UsersPage = lazy(() => import('./pages/Users'))
const SettingsPage = lazy(() => import('./pages/Settings'))
const Account = lazy(() => import('./pages/Account'))

function Protected() {
  const { user, isLoading } = useAuth()
  if (isLoading) return <Loading />
  if (!user) return <Navigate to="/login" replace />
  return (
    <Layout>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/apps" element={<Apps />} />
          <Route path="/apps/:id" element={<AppDetail />} />
          <Route path="/apps/:id/:tab" element={<AppDetail />} />
          <Route path="/stacks" element={<Stacks />} />
          <Route path="/containers" element={<Containers />} />
          <Route path="/platform" element={<Platform />} />
          <Route path="/proxy" element={<Proxy />} />
          <Route path="/cloudflare" element={<Cloudflare />} />
          <Route path="/backups" element={<Backups />} />
          <Route path="/storage" element={<Storage />} />
          <Route path="/security" element={<Security />} />
          <Route path="/claude" element={<Claude />} />
          <Route path="/claude/:section" element={<Claude />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/jobs/:id" element={<Jobs />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/account" element={<Account />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </Layout>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/*" element={<Protected />} />
      </Routes>
    </AuthProvider>
  )
}
