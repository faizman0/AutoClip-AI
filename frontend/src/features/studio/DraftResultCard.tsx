import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import StudioDownloadLink from './StudioDownloadLink'
import { useNavigate } from 'react-router-dom'
import { Btn, fmtDuration } from '../../ui'
import { studioApi } from './api'
import { Draft, RenderJob, draftDuration, languages } from './types'
import { draftExportState, ExportStatus } from './draftExportState'

const statusLabels: Record<ExportStatus, string> = {
  draft: 'Draft · Pending export', queued: 'Queued', rendering: 'Exporting',
  ready: 'Exported · Downloadable', failed: 'Export failed', updated: 'Modified · Re-export required',
}

export default function DraftResultCard({ projectId, draft, jobs, onEdit, onExport, onHistory }: {
  projectId: string; draft: Draft; jobs: RenderJob[]
  onEdit: () => void; onExport: () => void; onHistory: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const state = draftExportState(draft, jobs)
  const label = t(statusLabels[state.status])
  const completed = state.completed
  const thumbnail = studioApi.thumbnail(projectId, draft.id, draft.revision, completed?.job_id)
  const [failedThumbnail, setFailedThumbnail] = useState('')
  return <article className="ac-card">
    <button className={`ac-card-thumb studio-thumb${completed ? " studio-thumb--exported" : ""}`} onClick={onEdit} aria-label={t('Preview {{title}}', { title: draft.title })}>
      {failedThumbnail === thumbnail ? <span className="studio-thumbnail-fallback">{t("Thumbnail temporarily unavailable · Click to view")}</span> : <img key={thumbnail} src={thumbnail} alt="" loading="lazy" onError={() => setFailedThumbnail(thumbnail)} />}
      <span className="play">▷</span>
      <span className="ac-tag ac-tag--tl">{t(draft.origin==='visual-promo'?'Promo video':draft.origin==='visual-highlight'?'Highlight reel':'Final cut draft')}</span>
      <span className="ac-tag ac-tag--br">{fmtDuration(draftDuration(draft))}</span>
    </button>
    <div className="ac-card-body">
      <h2 className="ac-card-title">{draft.title}</h2>
      <div className={`studio-output-state studio-output-state--${state.status}`} role="status">
        {label}{state.active ? ` · ${state.active.percent ?? 0}%` : ''}
      </div>
      <p className="studio-output-hint">{completed ? t('Current V{{revision}} final video frames', { revision: draft.revision }) : t('Original thumbnail · Packaging effects are subject to the export')}</p>
      <div className="ac-card-desc">{draft.hook||draft.scenes[0].evidence||t('Keep the original audio and complete event')}</div>
      {state.status==='failed' && <p className="studio-output-hint studio-error">{state.failure?.error || t('Please retry the export; the original draft has been preserved')}</p>}
      {state.status==='ready' && state.failure && <p className="studio-output-hint">{t('A previous export of this version failed, but the completed file is still downloadable.')}</p>}
      {state.previous && !completed && <button className="studio-link studio-output-hint" onClick={onHistory}>{t('View the previously exported V{{revision}} old version', { revision: state.previous.revision })}</button>}
      <div className="ac-card-foot">
        <span className="meta">{draft.language === 'source' ? t('Original language') : languages.find(l=>l.value===draft.language)?.label ?? draft.language} · V{draft.revision} · {t('{{count}} segments', { count: draft.scenes.length })}</span>
        <div className="ac-card-actions">
          <Btn variant="text" onClick={onEdit}>{t('Preview & edit')}</Btn>
          {completed && <Btn variant="text" onClick={() => navigate(`/project/${projectId}/publish/studio-${completed.job_id}`)}>{t('Publish')}</Btn>}
          {completed ? <StudioDownloadLink className="studio-link" projectId={projectId} jobId={completed.job_id}/>
            : state.active ? <Btn variant="text" onClick={onHistory}>{t('View progress')}</Btn>
            : <Btn variant="text" onClick={onExport}>{t(state.status==='failed'?'Retry export':'Export final video')}</Btn>}
        </div>
      </div>
    </div>
  </article>
}
