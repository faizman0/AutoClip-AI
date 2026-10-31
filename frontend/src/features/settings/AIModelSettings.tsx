import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Select, Switch } from 'antd'
import { t } from '../../i18n'
import { Btn, Row, Section, Segmented, StatusDot } from '../../ui'
import SpeechRecognitionConfig from '../../components/SpeechRecognitionConfig'
import { PROVIDERS, providerPickerOptions, type ProviderKey } from './providers'
import { useModelSettings, type ModelSettingsStore } from './useModelSettings'
import { connectionOf, isTextOnly, mainConnection, presetKey, type SaveIssue } from './modelSettingsLogic'
import ProviderFields from './ProviderFields'
import ModelPicker from './ModelPicker'
import api from '../../services/api'

type SenseVoiceStatus = { status: 'not_installed' | 'installing' | 'ready' | 'error'; message: string }

function SenseVoiceConfig() {
  const [runtime, setRuntime] = useState<SenseVoiceStatus | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true
    const refresh = async () => {
      try {
        const value = await api.get<unknown, SenseVoiceStatus>('/sensevoice/status')
        if (active) { setRuntime(value); setError('') }
      } catch { if (active) setError(t('Unable to read the local model state at this time.')) }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 3000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])
  const act = async (remove = false) => {
    setBusy(true)
    try {
      const value = remove ? await api.delete<unknown, SenseVoiceStatus>('/sensevoice')
        : await api.post<unknown, SenseVoiceStatus>('/sensevoice/prepare')
      setRuntime(value); setError('')
    } catch { setError(t('Unable to complete the operation at this time.')) }
    finally { setBusy(false) }
  }
  const preparing = runtime?.status === 'installing'
  return <div className="ac-rows">
    <Row label={t('Transcription Model')} hint={t('Supports Chinese, Cantonese, English, Japanese, and Korean, generating subtitles with word-level timestamps.')}><span>SenseVoiceSmall</span></Row>
    <Row label={runtime?.status === 'ready' ? t('Model is Ready') : t('Prepare Local Model')}
      hint={runtime?.status === 'ready' ? t('After saving the settings, new tasks will use this model.') : t('First-time setup requires an internet connection to download components and models. Please reserve at least 8 GB of disk space; transcription will not upload audio after preparation.')}>
      {runtime?.status === 'ready' ? <StatusDot tone="ok" label={t('Ready')} />
        : preparing ? <StatusDot tone="accent" label={t('Preparing Components and Models…')} />
        : <Btn size="sm" disabled={!runtime || busy} onClick={() => void act()}>{t('Prepare Model')}</Btn>}
    </Row>
    {(error || runtime?.status === 'error') && <p className="ac-note" role="alert">{error || t('Unable to prepare the model. Please check your network connection and disk space.')}</p>}
    <p className="ac-note">{t('Only installs the required models, does not enable speaker diarization.')} <a href="https://huggingface.co/FunAudioLLM/SenseVoiceSmall" target="_blank" rel="noreferrer">{t('Model and License Information')}</a></p>
    {runtime?.status === 'ready' && <details className="ac-disclosure"><summary>{t('Advanced')}</summary>
      <Row label={t('Delete SenseVoice Components and Models')} hint={t('Deleting will require re-downloading to use again.')}>
        <Btn variant="danger" size="sm" disabled={busy} onClick={() => void act(true)}>{t('Delete')}</Btn>
      </Row>
    </details>}
  </div>
}

const ANCHORS: Record<string, string> = { model: 'ai-model', analysis: 'ai-model', vision: 'ai-model', speech: 'ai-speech', cover: 'ai-cover' }
const issueAnchor = (issue: SaveIssue) => issue.role === 'transcription' ? 'ai-speech' : issue.role === 'cover' ? 'ai-cover' : 'ai-model'

/**
 * Main AI service: provider, key, model (auto-recommended) and the frame-sampling switch.
 * Shared with the first-run card so both paths behave identically.
 */
export function AIServiceFields({ m, placement, showVisual = true }: { m: ModelSettingsStore; placement: 'settings_model' | 'home_setup'; showVisual?: boolean }) {
  const { settings, lists, busy, listErrors } = m
  const main = settings && mainConnection(settings)
  if (!settings) return null
  // First run: nothing is pre-selected. Key and model rows appear once a provider is chosen.
  if (!main) return <ProviderFields placement={placement} onChoose={p => m.chooseProvider('analysis', p)} onEdit={() => undefined} />
  const list = lists[main.id]
  const model = settings.analysis?.model || ''
  const hint = busy[main.id] && !model ? t('Fetching available models…')
    : model && list && !list.preview ? t('The recommended model has been selected for you and can be changed at any time.')
    : t('Enter your API Key to automatically select the recommended model.')
  return <>
    <ProviderFields connection={main} placement={placement} onChoose={p => m.chooseProvider('analysis', p)} onEdit={patch => m.editConnection(main, patch)} />
    <Row wide label={t('Model')} hint={hint}>
      <ModelPicker role="analysis" model={model} capability={settings.analysis?.capability} connection={main} list={list} busy={!!busy[main.id]} listError={listErrors[main.id]} mode={settings.analysis_mode}
        onChange={value => m.update({ analysis: { connection_id: main.id, model: value, capability: 'auto' } })}
        onCapability={capability => settings.analysis && m.update({ analysis: { ...settings.analysis, capability } })}
        onRefresh={() => void m.discover(main, true)}
        actions={<Btn variant="text" size="sm" disabled={!model} loading={m.testing} onClick={() => void m.test()}>{t('Test Connection')}</Btn>} />
    </Row>
    {showVisual && <Row label={t('Visual Recognition')} hint={isTextOnly(settings, lists)
      ? t('The current model only supports text, so it can only split based on subtitles. For games and content with less voiceover, consider switching to a multimodal model before enabling.')
      : t('Sample a few frames to understand actions and scenes. Recommended for games and content with less voiceover, providing more accurate splitting; slightly higher cost. Only subtitles will be sent if disabled.')}>
      <Switch checked={settings.analysis_mode !== 'subtitle'} onChange={m.setVisual} />
    </Row>}
  </>
}

function TranscriptionSection({ m }: { m: ModelSettingsStore }) {
  const { settings, lists, busy, listErrors } = m
  if (!settings) return null
  const cloud = settings.transcription?.provider === 'cloud'
  const sensevoice = settings.transcription?.provider === 'sensevoice_local'
  const connection = cloud ? connectionOf(settings, 'transcription') : undefined
  const main = mainConnection(settings)
  const options = [
    { label: t('Local Execution'), options: [{ value: 'whisper_local', label: t('Whisper · Local') }, { value: 'sensevoice_local', label: t('SenseVoice · Local') }] },
    ...providerPickerOptions().map(group => ({ ...group, options: group.options.filter(p => ['openai', 'dashscope', 'infistar', 'api88', 'glm', 'compatible'].includes(p.value)) })).filter(group => group.options.length),
  ]
  return <div id="ai-speech" className="ac-model-section">
    <h3 className="ac-model-section-title">{t('Transcription')}</h3>
    <p className="ac-note">{t('When the video has no subtitles, convert the speech to subtitles first. By default, this is done locally for free, without uploading audio.')}</p>
    <Row wide label={t('Transcription Method')} hint={t('Local transcription is free, but requires downloading the model on first use; cloud transcription requires no download and is billed based on usage.')}>
      <Select aria-label={t('Transcription Method')} style={{ width: '100%' }} value={cloud ? connection?.provider : sensevoice ? 'sensevoice_local' : 'whisper_local'} options={options}
        onChange={value => value === 'sensevoice_local' ? m.update({ transcription: { provider: 'sensevoice_local', model: 'SenseVoiceSmall' } }) : value === 'whisper_local' ? m.setTranscriptionLocal(settings.transcription?.model && settings.transcription.provider === 'whisper_local' ? settings.transcription.model : 'base') : m.chooseProvider('transcription', value as ProviderKey)}
        optionRender={option => <span>{option.value === 'whisper_local' ? t('Whisper · Local') : option.value === 'sensevoice_local' ? t('SenseVoice · Local') : PROVIDERS[option.value as ProviderKey]?.name}{PROVIDERS[option.value as ProviderKey]?.sponsor && <small style={{ marginLeft: 8, color: 'var(--sub)' }}>{t('Sponsor')} · {PROVIDERS[option.value as ProviderKey]?.sponsor?.offer}</small>}</span>} />
    </Row>
    {cloud && connection ? <>
      <ProviderFields connection={connection} ariaPrefix={t('Transcription')} hideProvider placement="settings_model"
        sharedKey={connection.id === main?.id && !!(connection.api_key || connection.has_key)}
        onChoose={p => m.chooseProvider('transcription', p)} onEdit={patch => m.editConnection(connection, patch)} />
      <Row wide label={t('Transcription Model')} hint={t('Send audio to the selected service for transcription, billed by the service provider.')}>
        <ModelPicker role="transcription" model={settings.transcription?.model || ''} connection={connection} list={lists[connection.id]} busy={!!busy[connection.id]} listError={listErrors[connection.id]} mode={settings.analysis_mode}
          onChange={model => m.update({ transcription: { provider: 'cloud', connection_id: connection.id, model } })} onRefresh={() => void m.discover(connection, true)} />
      </Row>
    </> : sensevoice ? <SenseVoiceConfig /> : <SpeechRecognitionConfig hideProvider selectedModel={settings.transcription?.model || 'base'} onModelChange={m.setTranscriptionLocal} />}
  </div>
}

function CoverSection({ m }: { m: ModelSettingsStore }) {
  const { settings, lists, busy, listErrors } = m
  if (!settings) return null
  const main = mainConnection(settings)
  const connection = connectionOf(settings, 'cover') || main
  const separate = !!settings.cover && settings.cover.connection_id !== main?.id
  const mainHasImage = !!lists[main?.id || '']?.models.some(x => x.image)
  return <div id="ai-cover" className="ac-model-section">
    <h3 className="ac-model-section-title">{t('Cover')}</h3>
    <p className="ac-note">{t('By default, The clearest possible image is automatically used to design the cover, free and instant. If you want more design sense, you can use AI-generated covers, with image generation services and models chosen by you, billed based on the selected service.')}</p>
    <Row wide label={t('Cover Source')}>
      <Segmented ariaLabel={t('Cover Source')} value={settings.cover_enabled ? 'ai' : 'frame'} options={[{ value: 'frame', label: t('Auto Design (Free)') }, { value: 'ai', label: t('AI Generated') }]} onChange={value => m.setCoverEnabled(value === 'ai')} />
    </Row>
    {settings.cover_enabled && !main && <p className="ac-note">{t('First select an AI service above, and the image generation model will be automatically selected.')}</p>}
    {settings.cover_enabled && main && <>
      <Row label={t('Use Different Service for Cover')} hint={separate || mainHasImage || !lists[main?.id || ''] ? t('Defaults to sharing the API Key with the AI service, no need to fill in again.') : t('{{name}} does not have an image generation model, you can select a different service for the cover.', { name: PROVIDERS[presetKey(main!)]?.name || main?.provider })}>
        <Switch checked={separate} onChange={m.setCoverSeparate} />
      </Row>
      {separate && connection && <ProviderFields connection={connection} ariaPrefix={t('Cover')} placement="settings_model" onChoose={p => m.chooseProvider('cover', p)} onEdit={patch => m.editConnection(connection, patch)} />}
      {connection && <Row wide label={t('Image Generation Model')} hint={t('The recommended image generation model has been automatically selected, but you can change it.')}>
        <ModelPicker role="cover" model={settings.cover?.model || ''} connection={connection} list={lists[connection.id]} busy={!!busy[connection.id]} listError={listErrors[connection.id]} mode={settings.analysis_mode}
          onChange={model => m.setCoverModel(connection.id, model)} onRefresh={() => void m.discover(connection, true)} />
      </Row>}
      <Row label={t('Reference Video Frame')} hint={t('When enabled, a video screenshot will be sent to the cover model for more content-appropriate results. This will slightly increase the cost of cover generation.')}>
        <Switch checked={settings.allow_send_frame} onChange={value => m.update({ allow_send_frame: value })} />
      </Row>
    </>}
  </div>
}

export default function AIModelSettings({ requestedSection }: { requestedSection: string }) {
  useTranslation()
  const m = useModelSettings()
  const { settings } = m
  useEffect(() => {
    const anchor = ANCHORS[requestedSection]
    if (!settings || !anchor) return
    const timer = window.setTimeout(() => document.getElementById(anchor)?.scrollIntoView({ block: 'start' }), 0)
    return () => window.clearTimeout(timer)
  }, [requestedSection, !!settings])

  if (!settings) return <Section title={t('AI 模型')}><p className="ac-note">{m.error || t('加载中…')}</p>{m.error && <Btn onClick={() => void m.load()}>{t('重试')}</Btn>}</Section>
  const save = async () => {
    const result = await m.save()
    if (result.issue) document.getElementById(issueAnchor(result.issue))?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }
  return <div className="ac-ai-settings">
    {settings.migration_warnings?.includes('cover_configuration_invalid') && <p role="alert" className="ac-note">{t('The previous cover configuration is invalid; please re-select the service in the cover settings. The original configuration file has been preserved.')}</p>}
    {settings.migration_warnings?.some(code => ['settings_configuration_invalid', 'analysis_configuration_invalid', 'vision_configuration_invalid', 'transcription_configuration_invalid'].includes(code)) && <p role="alert" className="ac-note">{t('部分 AI 配置无法读取，请重新选择服务并填写密钥。原配置已保留，保存修正时会备份损坏的文件。')}</p>}
    <fieldset className="ac-model-fields" disabled={m.saving}>
      <div id="ai-model" className="ac-model-section">
        <h3 className="ac-model-section-title">{t('AI 服务')}</h3>
        <p className="ac-note">{t('Select a service and fill in the API Key, and the model will be automatically selected. Subtitles and covers will use this key by default.')}</p>
        <AIServiceFields m={m} placement="settings_model" />
      </div>
      <TranscriptionSection m={m} />
      <CoverSection m={m} />
      <details className="ac-disclosure ac-model-section"><summary>{t('Advanced')}</summary>
        <Row label={t('Text Chunk Size')}><input aria-label={t('Text Chunk Size')} className="ac-input" type="number" min={1000} max={10000} step={500} value={settings.chunk_size} onChange={e => m.update({ chunk_size: Number(e.target.value) })} /></Row>
        <Row label={t('Minimum Score Threshold')}><input aria-label={t('Minimum Score Threshold')} className="ac-input" type="number" min={.1} max={1} step={.05} value={settings.min_score_threshold} onChange={e => m.update({ min_score_threshold: Number(e.target.value) })} /></Row>
        <Row label={t('Maximum Clips Per Collection')}><input aria-label={t('Maximum Clips Per Collection')} className="ac-input" type="number" min={1} max={20} value={settings.max_clips_per_collection} onChange={e => m.update({ max_clips_per_collection: Number(e.target.value) })} /></Row>
      </details>
    </fieldset>
    {m.error && <p className="studio-error" role="alert">{m.error}</p>}
    <div className="ac-savebar">
      <StatusDot tone={m.dirty ? 'muted' : 'ok'} label={m.dirty ? t('There are unsaved changes') : settings.saved ? t('Settings saved') : t('First-time use, settings will take effect after saving')} />
      <Btn variant="cta" loading={m.saving} onClick={() => void save()}>{t('Save Settings')}</Btn>
    </div>
  </div>
}
