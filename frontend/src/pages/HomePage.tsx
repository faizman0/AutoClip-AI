import { t } from '../i18n'
import { useTranslation } from 'react-i18next'
import { useState, useEffect } from 'react'
import { 
  Layout, 
  Typography, 
  Select, 
  Spin, 
  message 
} from 'antd'
import { useNavigate } from 'react-router-dom'
import ProjectCard from '../components/ProjectCard'
import CreativeImport from '../features/studio/CreativeImport'
import FirstRunSetup from '../features/settings/FirstRunSetup'


import { projectApi } from '../services/api'
import { openExample as startExampleProject } from '../features/studio/openExample'
import { modelSettingsApi, type ModelSettings } from '../features/settings/modelSettingsApi'
import { forSave } from '../features/settings/modelSettingsLogic'
import { syncFollowingCover } from '../features/settings/modelDefaults'
import { recommendTimelineReplacement } from '../features/settings/timelineReplacement'
import { analysisProviderId, listSwitchModelOptions, usableConnectionCount } from '../features/studio/switchModelOptions'
import { studioApi } from '../features/studio/api'
import { Btn } from '../ui'
import { useSimpleProgressStore } from '../stores/useSimpleProgressStore'
import { Project, useProjectStore } from '../store/useProjectStore'
import { useProjectPolling } from '../hooks/useProjectPolling'
import { apiConfigManager } from '../utils/apiConfig'

const { Content } = Layout
const { Title, Text } = Typography
const { Option } = Select

/** Stable identity for useProjectPolling restart binding (avoids resubscribe churn). */
function subscribeBackendRestart(onRestart: () => void) {
  return apiConfigManager.addListener(config => {
    if (config.isReady) onRestart()
  })
}

export default function HomePage() {
  useTranslation()
  const navigate = useNavigate()
  const { projects, setProjects, deleteProject, loading, setLoading } = useProjectStore()
  const [statusFilter, setStatusFilter] = useState<string>('all')
  // null until the model document has loaded; true blocks import until the first AI service is saved.
  const [setupNeeded, setSetupNeeded] = useState<boolean | null>(null)
  const [setupRequest, setSetupRequest] = useState(0)
  const [exampleBusy, setExampleBusy] = useState(false)
  const [aiSettings, setAiSettings] = useState<ModelSettings | null>(null)

  // Use the project polling hook
  useProjectPolling({
    onProjectsUpdate: (updatedProjects) => {
      setProjects(updatedProjects || [])
    },
    enabled: true,
    interval: 30000, // Poll every 30 seconds to reduce frequent requests
    // Mirror studio useWorkspace (#502): shell backend-started → refresh list now.
    // Module fn keeps the subscribe identity stable across HomePage re-renders.
    subscribeRestart: subscribeBackendRestart,
  })

  // Global safety net: when there are no running projects, force-stop progress polling and clear the cache
  useEffect(() => {
    const hasActive = projects.some(p => p.status === 'processing' || p.status === 'pending')
    if (!hasActive) {
      try {
        const { stopPolling, clearAllProgress } = useSimpleProgressStore.getState()
        stopPolling()
        clearAllProgress()
        console.log('No running projects; globally stopped progress polling and cleared the progress cache')
      } catch (e) {
        console.warn('A problem occurred while stopping global progress polling:', e)
      }
    }
  }, [projects])

  useEffect(() => {
    // Delay loading projects to avoid firing a large number of requests immediately at startup
    const timer = setTimeout(() => {
      loadProjects()
    }, 1000) // Delay by 1 second
    
    return () => clearTimeout(timer)
  }, [])

  const loadProjects = async () => {
    setLoading(true)
    try {
      // Fetch real project data from the backend API
      const projects = await projectApi.getProjects()
      // Ensure projects is an array type
      const safeProjects = Array.isArray(projects) ? projects : []
      setProjects(safeProjects)
    } catch (error) {
      message.error(t("Failed to load projects"))
      console.error('Load projects error:', error)
      // If the API call fails, set an empty array
      setProjects([])
    } finally {
      setLoading(false)
    }
  }

  const handleDeleteProject = async (id: string) => {
    try {
      await projectApi.deleteProject(id)
      deleteProject(id)
      message.success(t("Project deleted successfully"))
    } catch (error) {
      message.error(t("Failed to delete project"))
      console.error('Delete project error:', error)
    }
  }

  // Called by ProjectCard when the user manually clicks retry and the retry request has already succeeded.
  // ProjectCard.handleRetry has already sent the start/retryProcessing request; here we only handle
  // the toast + refreshing the list, and must never send another retry request (which would stack with the card's own request and create
  // a loadProjects→remount→auto-start loop).
  useEffect(() => {
    let live = true
    modelSettingsApi.get().then(value => { if (live) setAiSettings(value) }).catch(() => { if (live) setAiSettings(null) })
    return () => { live = false }
  }, [])

  const replacement = recommendTimelineReplacement(aiSettings)

  const switchOptions = listSwitchModelOptions(aiSettings, {
    connectionId: aiSettings?.analysis?.connection_id,
    model: aiSettings?.analysis?.model,
  })

  /** X2: accept the dialog's connectionId/model; fall back to timeline replacement only when omitted. */
  const handleSwitchModelAndRetry = async (
    projectId: string,
    choice?: { connectionId: string; model: string },
  ) => {
    const nextModel = choice || recommendTimelineReplacement(aiSettings)
    const project = projects.find(item => item.id === projectId)
    if (!aiSettings || !nextModel || !project) {
      message.error(t("Retry failed, please try again later"))
      return
    }
    const managed = !!(project.settings?.smart_import || project.processing_config?.smart_import)
      || ['highlight', 'promo'].includes(project.settings?.creative?.goal || project.processing_config?.creative?.goal)
    try {
      const saved = await modelSettingsApi.save(forSave(syncFollowingCover(aiSettings, {
        ...aiSettings,
        analysis: {
          connection_id: nextModel.connectionId,
          model: nextModel.model,
          capability: aiSettings.analysis?.capability || 'auto',
          model_source: 'user',
        },
      })))
      setAiSettings(saved)
      if (managed) await studioApi.retry(projectId, { retry_kind: 'switch_model' })
      else await projectApi.retryProcessing(projectId)
      message.success(t("Started retrying project processing"))
      await loadProjects()
    } catch (error) {
      message.error(t("Retry failed, please try again later"))
      console.error('Switch model and retry error:', error)
    }
  }

  const handleRetryProject = async () => {
    message.success(t("Started retrying project processing"))
    try {
      await loadProjects()
    } catch (error) {
      console.error('Refresh after retry error:', error)
    }
  }

  const openExample = async () => {
    setExampleBusy(true)
    try {
      await startExampleProject(navigate)
      void loadProjects().catch(() => undefined)
    } catch (error) {
      message.error(t("The example project is temporarily unavailable"))
      console.error('Example project error:', error)
    } finally {
      setExampleBusy(false)
    }
  }

  const handleProjectCardClick = (project: Project) => {
    // Projects in importing state cannot be clicked into the details page
    if (project.status === 'pending' && !project.settings?.smart_import && !project.processing_config?.smart_import) {
      message.warning(t("The project is importing, please check the details later"))
      return
    }
    
    // Other states can normally enter the details page
    navigate(`/project/${project.id}`)
  }

  const pendingImports = projects.filter(p => p.settings?.import_staging || p.processing_config?.import_staging)
  const filteredProjects = (projects || [])
    .filter(p => !p.settings?.import_staging && !p.processing_config?.import_staging)
    .filter(project => {
      const matchesStatus = !statusFilter || statusFilter === 'all' || project.status === statusFilter || (statusFilter === 'error' && project.status === 'failed')
      return matchesStatus
    })
    .sort((a, b) => {
      // Sort by creation time in descending order, newest first
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    })

  return (
    <Layout style={{
      minHeight: '100vh',
      background: 'var(--ac-bg)'
    }}>
      <Content style={{ padding: '40px 56px 56px', position: 'relative' }}>
        <div style={{ maxWidth: '1200px', margin: '0 auto', position: 'relative' }}>
          <FirstRunSetup onStatus={setSetupNeeded} openRequest={setupRequest} />
          <CreativeImport onImported={loadProjects} blocked={setupNeeded === true} onBlocked={() => setSetupRequest(n => n + 1)} onExample={() => void openExample()} exampleBusy={exampleBusy} />
          {pendingImports.length>0&&<div className="studio-import-resume"><b>{t('Unfinished imports')}</b>{pendingImports.map(p=><button key={p.id} className="studio-link" onClick={()=>navigate(`/import/${p.id}`)}>{p.name} · {t('Continue import confirmation')}</button>)}</div>}

          {/* Project management area */}
          <div style={{
            background: 'transparent',
            padding: '0',
            marginBottom: '32px'
          }}>
            {/* Project list header area */}
            <div style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              marginTop: '56px',
              marginBottom: '22px'
            }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px' }}>
                <Title
                  level={2}
                  style={{ margin: 0, color: 'var(--ac-ink)', fontSize: '16px', fontWeight: 600 }}
                >{t("My projects")}</Title>
                <Text style={{ color: 'var(--ac-muted)', fontSize: '13px' }}>
                  {filteredProjects.length}
                </Text>
              </div>
              
              {/* Status filter moved to the right */}
              <div style={{ 
                display: 'flex', 
                alignItems: 'center'
              }}>
                <Select
                  placeholder={t("All statuses")}
                  value={statusFilter}
                  onChange={setStatusFilter}
                  variant="borderless"
                  style={{ minWidth: '120px', fontSize: '13px' }}
                  suffixIcon={<span style={{ color: 'var(--ac-muted)', fontSize: '10px' }}>⌄</span>}
                  allowClear
                >
                  <Option value="all">{t("All statuses")}</Option>
                  <Option value="completed">{t("Completed")}</Option>
                  <Option value="processing">{t("Processing")}</Option>
                  <Option value="error">{t("Processing failed")}</Option>
                </Select>
              </div>
            </div>

            {/* Project list content */}
             <div>
               {loading ? (
                 <div style={{
                   textAlign: 'center',
                   padding: '72px 0',
                   background: 'var(--ac-card)',
                   borderRadius: '16px',
                   border: '1px solid var(--ac-line)'
                 }}>
                   <Spin size="large" />
                   <div style={{ marginTop: '18px', color: 'var(--ac-muted)', fontSize: '14px' }}>{t("Loading project list…")}</div>
                 </div>
               ) : filteredProjects.length === 0 ? (
                 projects.length === 0 ? (
                   /* Empty state per DESIGN.md: one sentence + one thing to do. The example project does not need a key, so you can see the result first and then import your own video. */
                   <div className="ac-empty">
                     <b>{t("No projects yet")}</b>
                     <span>{t("Import a video from above, or try the example project first to see the output.")}</span>
                     <div style={{ marginTop: 18 }}>
                       <Btn size="sm" loading={exampleBusy} onClick={() => void openExample()}>{t("See the result with the example project")}</Btn>
                     </div>
                   </div>
                 ) : (
                   <div className="ac-empty"><b>{t("No matching projects found")}</b></div>
                 )
               ) : (
                 <div style={{
                   display: 'grid',
                   gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
                   gap: '24px',
                   justifyContent: 'start'
                 }}>
                   {filteredProjects.map((project: Project) => (
                     <div key={project.id} style={{ position: 'relative', zIndex: 1 }}>
                       <ProjectCard 
                         project={project} 
                         onDelete={handleDeleteProject}
                         onRetry={() => handleRetryProject()}
                         onClick={() => handleProjectCardClick(project)}
                         replacement={replacement}
                         switchOptions={switchOptions}
                         connectionCount={usableConnectionCount(aiSettings)}
                         analysisProvider={analysisProviderId(aiSettings)}
                         onSwitchAndRetry={handleSwitchModelAndRetry}
                       />
                     </div>
                   ))}
                 </div>
               )}
             </div>
           </div>
         </div>
      </Content>
    </Layout>
  )
}
