import { trackExperience } from '../../analytics/experience'
import { useTranslation } from 'react-i18next'
import { t } from '../../i18n'
import { useEffect, useRef, useState } from 'react'
import { Select } from 'antd'
import { useNavigate } from 'react-router-dom'
import { Btn, Segmented, Dialog, StatusDot } from '../../ui'
import PlatformPicker from './PlatformPicker'
import { studioApi, errorText } from './api'
import { trackAsrReadiness, trackQuickOutputPlatforms } from '../../analytics/studio'
import { featureSnapshot, useFlag } from '../../analytics/flags'
import TemplatePicker, { templateImportFields, type HtmlTemplateChoice } from './TemplatePicker'
import StyleProbeLine from './StyleProbeLine'
import { readStyle } from './editingStyleSession'
import { defaultImportOptions, ImportOptions } from './types'
import ImportPreferences from './ImportPreferences'
import { speechApi } from '../../services/api'
import { asrReadinessProperties, repairDestination, submissionBlock, visibleIssues, type ImportReadiness, type ReadinessIssue } from './importReadiness'
import './studio.css'
import './quick-output.css'

function issueCopy(item: ReadinessIssue) {
  if (item.key === 'analysis') return t('The analysis model is not set up yet. Connect an AI service before generating.')
  if (item.code === 'whisper_installing') return t('Installing local transcription…')
  if (item.code === 'whisper_not_installed') return t('Local transcription is not installed yet. If you already have subtitles, you can continue directly.')
  if (item.code === 'whisper_model_missing') return t('The transcription model has not been downloaded yet. If you already have subtitles, you can continue directly.')
  if (item.code === 'whisper_model_downloading') return t('Downloading the transcription model…')
  if (item.code === 'whisper_model_failed') return t('The transcription model download failed. Please retry. If you already have subtitles, you can continue directly.')
  if (item.code === 'sensevoice_not_ready') return t('SenseVoice is not ready yet. If you already have subtitles, you can continue directly.')
  if (item.code === 'cloud_not_configured') return t('Cloud transcription is not configured yet. If you already have subtitles, you can continue directly.')
  if (item.key === 'visual') return t('Visual analysis requires an available vision model.')
  if (item.key === 'ffmpeg') return t('FFmpeg not found; unable to process the video for now.')
  return t('Import conditions are not ready yet.')
}

function repairLabel(item: ReadinessIssue) {
  if (item.repair === 'settings_ai') return t('Connect AI service')
  if (item.repair === 'install_whisper') return item.model ? t('Download transcription model') : t('Install Whisper')
  if (item.repair === 'settings_transcription') return t('Transcription settings')
  if (item.repair === 'settings_vision') return t('Vision settings')
  return ''
}

export default function CreativeImport({ onImported, blocked = false, onBlocked }: { onImported: () => Promise<void>; blocked?: boolean; onBlocked?: () => void }) {
  useTranslation()
  const navigate = useNavigate()
  const [source, setSource] = useState<'link' | 'file'>('link')
  const [url, setUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [subtitle, setSubtitle] = useState<File | null>(null)
  const [options, setOptions] = useState<ImportOptions>({...defaultImportOptions})
  const templatesOn = useFlag('pkg_templates_v1') === true
  const visualOn = useFlag('pkg_template_picker_visual') === true
  const [browser, setBrowser] = useState('')
  const [platforms, setPlatforms] = useState<string[]>(['douyin'])
  const [preferences, setPreferences] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [readiness, setReadiness] = useState<ImportReadiness | null>(null)
  const [settled, setSettled] = useState(false)
  const [repairing, setRepairing] = useState(false)
  // Installing the runtime is only half of local transcription: download the model right after.
  const modelAfterRuntime = useRef(false)
  useEffect(() => {
    let cancel = false
    studioApi.readiness()
      .then(value => {
        if (cancel) return
        setReadiness(value)
        trackAsrReadiness(asrReadinessProperties(value, false))
      })
      .catch(() => { if (!cancel) setReadiness(null) })
      .finally(() => { if (!cancel) setSettled(true) })
    return () => { cancel = true }
  }, [])
  useEffect(() => {
    const check = readiness?.checks?.transcription
    if (check?.code === 'whisper_model_missing' && check.model && modelAfterRuntime.current) {
      modelAfterRuntime.current = false
      void downloadModel(check.model)
      return
    }
    if (check?.code === 'whisper_install_failed') modelAfterRuntime.current = false
    if (check?.code !== 'whisper_installing' && check?.code !== 'whisper_model_downloading') return
    const timer = window.setInterval(() => {
      studioApi.readiness().then(setReadiness).catch(() => undefined)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [readiness?.checks?.transcription?.code])
  const issues = visibleIssues(readiness, !!subtitle)
  const downloadModel = async (model: string) => {
    setRepairing(true)
    setError('')
    try {
      await speechApi.downloadModel(model)
      setReadiness(await studioApi.readiness())
      setSettled(true)
    } catch (e) { setError(t(errorText(e))) } finally { setRepairing(false) }
  }
  const repair = async (item: ReadinessIssue) => {
    if (item.repair === 'install_whisper' && item.model) { await downloadModel(item.model); return }
    if (item.repair === 'install_whisper') {
      modelAfterRuntime.current = true
      setRepairing(true)
      setError('')
      try {
        await speechApi.installRuntime()
        setReadiness(await studioApi.readiness())
        setSettled(true)
      } catch (e) { modelAfterRuntime.current = false; setError(t(errorText(e))) } finally { setRepairing(false) }
      return
    }
    if (item.repair === 'settings_ai' && blocked) { onBlocked?.(); return }
    const destination = repairDestination(item.repair)
    if (destination) navigate(destination)
  }
  const submit = async () => {
    if (blocked) { trackExperience('import_blocked', { reason: 'setup_required', placement: 'home_setup' }); setError(t("Please connect an AI service before importing a video.")); onBlocked?.(); return }
    if (source === 'file' ? !file : !url.trim()) { setError(t("Please add a video file or link first")); return }
    const reason = submissionBlock(readiness, !!subtitle, settled)
    if (reason === 'pending') { setError(t('Confirming import conditions…')); return }
    trackAsrReadiness(asrReadinessProperties(readiness, !!subtitle))
    if (reason === 'analysis') { setError(t('Please connect an AI service before importing a video.')); return }
    if (reason === 'transcription') { setError(t('No usable transcription is available yet. You can attach an SRT subtitle file, or prepare transcription first.')); return }
    if (reason === 'visual') { setError(t('Please configure a visual analysis model before generating the final video.')); return }
    if (reason === 'ffmpeg') { setError(t('FFmpeg not found; unable to generate the final video.')); return }
    setBusy(true); setError('')
    try {
      const body = new FormData()
      body.append('goal', options.goal); body.append('language', options.language); body.append('instruction', options.instruction)
      platforms.forEach(platform => body.append('platforms', platform))
      body.append('auto_start', 'true')
      body.append('portrait_style', options.portrait_style || 'auto')
      const portraitChosen = platforms.some(platform => !['bilibili', 'youtube_long', 'original'].includes(platform))
      const styleKey = templatesOn && visualOn && portraitChosen ? (source === 'file' ? (file ? `file:${file.name}:${file.size}:${file.lastModified}` : null) : (url.trim() ? `url:${url.trim()}` : null)) : null
      const recommendation = visualOn && styleKey && readStyle().key === styleKey ? readStyle().result?.template : undefined
      const templateFields = templateImportFields(templatesOn, options.html_template, recommendation)
      if (templateFields.html_template) body.append('html_template', templateFields.html_template)
      if (templateFields.recommended_template) body.append('recommended_template', templateFields.recommended_template)
      body.append('features', JSON.stringify(featureSnapshot()))
      if (options.aspect) body.append('aspect', options.aspect)
      if (options.duration) body.append('duration', String(options.duration))
      if (subtitle) body.append('subtitle', subtitle)
      body.append('name', file && source === 'file' ? file.name.replace(/\.[^.]+$/, '') : t("Smart edit"))
      if (source === 'file' && file) body.append('video', file)
      else { body.append('url', url.trim()); if (browser) body.append('browser', browser) }
      const result = await studioApi.import(body)
      trackQuickOutputPlatforms({ platform_count: platforms.length, portrait_style: options.portrait_style || 'auto' })
      // Project refresh should never make a successful import look like an upload failure.
      void onImported().catch(() => undefined)
      navigate(`/project/${result.project_id}`)
    } catch (e) { setError(t(errorText(e))) } finally { setBusy(false) }
  }
  return <section className="ac-creative-import">
    <h1 className="ac-title">{t("Drop in a video and generate a publish-ready final cut directly.")}</h1>
    <p className="studio-muted">{t("Choose the publishing platforms; AI will automatically select content, complete the editing and rendering. Once generated, you can download it directly or continue editing.")}</p>
    <div className="studio-import-box">
      <div className="studio-row"><Segmented ariaLabel={t("Import source")} value={source} onChange={v=>!busy&&setSource(v)} options={[{value:'link',label:t("Import by link")},{value:'file',label:t("Import by file")}]}/></div>
      {source==='link'?<label className="studio-field"><span className="studio-sr">{t("Video link")}</span><textarea placeholder={t("Paste a Bilibili or YouTube video link")} value={url} onChange={e=>setUrl(e.target.value)} disabled={busy}/></label>:<label className="studio-file"><span>{file?.name || t("Select a video")}</span><input type="file" aria-label={t("Video file")} accept="video/mp4,video/webm,video/quicktime,.mkv,.avi" disabled={busy} onChange={e=>setFile(e.target.files?.[0] || null)}/><small>MP4 / MOV / MKV / WEBM / AVI</small></label>}
      {templatesOn && visualOn && platforms.some(platform => !['bilibili', 'youtube_long', 'original'].includes(platform)) && <StyleProbeLine sourceKey={source === 'file' ? (file ? `file:${file.name}:${file.size}:${file.lastModified}` : null) : (url.trim() ? `url:${url.trim()}` : null)} />}
      <PlatformPicker value={platforms} onChange={setPlatforms} disabled={busy}/>
      {templatesOn && platforms.some(platform => !['bilibili', 'youtube_long', 'original'].includes(platform)) && <TemplatePicker visual={visualOn} disabled={busy} value={options.html_template} sourceKey={source === 'file' ? (file ? `file:${file.name}:${file.size}:${file.lastModified}` : null) : (url.trim() ? `url:${url.trim()}` : null)} url={url} file={file} sourceKind={source === 'file' ? (file ? 'file' : 'none') : (url.trim() ? 'link' : 'none')} onChange={(html_template: HtmlTemplateChoice) => setOptions({...options, html_template})}/>}
      {!templatesOn && platforms.some(platform => !['bilibili', 'youtube_long', 'original'].includes(platform)) && <div className="studio-field"><span>{t('Vertical layout')}</span><Select aria-label={t('Vertical layout')} disabled={busy} value={options.portrait_style || 'auto'} options={[{value:'auto',label:t('Platform default')},{value:'interview',label:t('Interview style (person window)')},{value:'podcast',label:t('Podcast style (full screen)')}]} onChange={portrait_style=>setOptions({...options,portrait_style})}/><small>{t('Only adjusts the vertical layout; subtitle and publish copy languages still follow the platform.')}</small></div>}
      {templatesOn && options.html_template === 'classic' && platforms.some(platform => !['bilibili', 'youtube_long', 'original'].includes(platform)) && <div className="studio-field"><span>{t('Vertical layout')}</span><Select aria-label={t('Vertical layout')} disabled={busy} value={options.portrait_style || 'auto'} options={[{value:'auto',label:t('Platform default')},{value:'interview',label:t('Interview style (person window)')},{value:'podcast',label:t('Podcast style (full screen)')}]} onChange={portrait_style=>setOptions({...options,portrait_style})}/><small>{t('Only adjusts the vertical layout; subtitle and publish copy languages still follow the platform.')}</small></div>}
      <details className="studio-details"><summary>{t("Any special requirements? (optional)")}</summary><label className="studio-field"><span className="studio-sr">{t("Production requirements")}</span><input aria-label={t("Production requirements")} placeholder={t("For example: keep the complete viewpoint or challenge process")} maxLength={1000} value={options.instruction} disabled={busy} onChange={e=>setOptions({...options,instruction:e.target.value})}/></label></details>
      {issues.length > 0 && <div className="studio-readiness" role="status">
        {issues.map(item => <div className="studio-readiness-row" key={item.key}>
          <StatusDot tone={item.code === 'whisper_installing' || item.code === 'whisper_model_downloading' ? 'accent' : 'muted'} label={issueCopy(item)} />
          {item.repair !== 'none' && <Btn size="sm" disabled={busy || repairing} loading={repairing && item.repair === 'install_whisper'} onClick={() => void repair(item)}>{repairLabel(item)}</Btn>}
        </div>)}
      </div>}
      <div className="studio-row studio-import-bottom"><div><span className="studio-muted">{t('It will automatically produce for the selected platforms; the quantity is determined by the source content.')}</span><button className="studio-link studio-preferences-link" disabled={busy} onClick={()=>setPreferences(true)}>{t("Advanced preferences")}</button></div><Btn variant="cta" loading={busy} onClick={submit}>{busy?t("Generating"):t("Generate final video")}</Btn></div>
      {error&&<p className="studio-error" role="alert">{error}</p>}
    </div>
    <Dialog open={preferences} onClose={()=>setPreferences(false)} title={t("Production preferences")} description={t("AI matches by default. Your specified options take priority, and you can modify them after generation.")} footer={<div className="studio-actions"><Btn onClick={()=>{setOptions({...defaultImportOptions,instruction:options.instruction});setSubtitle(null);setBrowser('')}}>{t("Restore automatic")}</Btn><Btn variant="cta" onClick={()=>setPreferences(false)}>{t("Done")}</Btn></div>}>
      <ImportPreferences value={options} onChange={setOptions}/>
      <label className="studio-field">{t("Existing subtitles (optional)")}<input type="file" accept=".srt" aria-label={t("Existing subtitles")} onChange={e=>setSubtitle(e.target.files?.[0] || null)}/>{subtitle&&<small>{subtitle.name}<button className="studio-link" onClick={()=>setSubtitle(null)}>{t("Remove")}</button></small>}</label>
      {source==='link'&&<details className="studio-details"><summary>{t("When the download hits a login restriction")}</summary><label className="studio-field">{t("Use browser login state")}<select value={browser} onChange={e=>setBrowser(e.target.value)}><option value="">{t("Do not use")}</option>{['chrome','edge','safari','firefox'].map(b=><option key={b}>{b}</option>)}</select></label><p className="studio-muted">{t("Only choose this when the download requires login; browser cookies are not read automatically.")}</p></details>}
    </Dialog>
  </section>
}
