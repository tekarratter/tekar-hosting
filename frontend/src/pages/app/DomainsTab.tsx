import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { api, type Route } from '../../api'
import { Section } from '../../components/ui'
import RouteForm from '../../components/RouteForm'
import { RouteTable } from '../../components/RouteTable'
import type { TabProps } from '../AppDetail'

export default function DomainsTab({ app, canDeploy }: TabProps) {
  const q = useQuery({ queryKey: ['routes', app.id], queryFn: () => api.get<Route[]>(`/api/proxy/routes?app_id=${app.id}`) })
  const [edit, setEdit] = useState<Route | null | undefined>(undefined)
  return (
    <Section title="Domain & proxy" actions={canDeploy && <button className="btn-primary btn-sm" onClick={() => setEdit(null)}><Plus className="h-3.5 w-3.5" />Thêm domain</button>}>
      <RouteTable routes={q.data || []} loading={q.isLoading} canEdit={canDeploy} onEdit={setEdit} onChanged={() => q.refetch()} hideApp />
      {edit !== undefined && <RouteForm open route={edit} appId={app.id} onClose={() => setEdit(undefined)} onSaved={() => q.refetch()} />}
    </Section>
  )
}
