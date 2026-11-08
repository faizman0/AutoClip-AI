import { trackExperience } from '../analytics/experience'
import { t } from '../i18n'
import { useTranslation } from 'react-i18next'
import React, { useState, useEffect, useMemo, useRef } from 'react'
import { Switch, message } from 'antd'
import { useLocation, useNavigate } from 'react-router-dom'
import { settingsApi } from '../services/api'
import AIModelSettings from '../features/settings/AIModelSettings'
import OutputBrandingSetting from '../features/settings/OutputBrandingSetting'
import OutputCaptionsSetting from '../features/settings/OutputCaptionsSetting'
import FeedbackDialog from '../components/FeedbackDialog'
import PublishSettings from '../components/PublishSettings'
import { isDesktopMode } from '../utils/desktopMode'
import { openExternalLink } from '../utils/externalLinks'
import { isAnalyticsEnabled, setAnalyticsEnabled } from '../analytics/posthog'
import { getRuntimeInfo } from '../analytics/lifecycle'
import { isCrashReportsEnabled, setCrashReportsEnabled } from '../desktop/sentry'
import { isKeepAwakeEnabled, getKeepAwakeController } from '../features/studio/keepAwake'
import { getAppVersion } from '../desktop/updater'
import { useAppUpdate } from '../desktop/UpdatePrompt'
import { FEEDBACK_DISCUSSIONS_URL, FEEDBACK_ISSUES_URL } from '../analytics/feedback'
import { useTheme } from '../context/ThemeContext'
import { Btn, Icon, Row, Section, Segmented } from '../ui'
import FlagOverrides, { flagOverridesVisible } from '../features/settings/FlagOverrides'
import ImportPreferenceSettings from '../features/settings/ImportPreferenceSettings'

type SectionKey = 'ai' | 'publish' | 'app' | 'feedback'
const NAV: Array<{ key: SectionKey; label: string }> = [
  { key: 'ai', get label() { return t('settings.providers.nav') } },
  { key: 'publish', get label() { return t("Publish") } },
  { key: 'app', get label() { return t("App") } },
  { key: 'feedback', get label() { return t("Feedback") } },
]
// Legacy links (project card "Model settings", "Transcription settings", etc.) can still be opened, and scroll to the corresponding section
const SECTION_ALIASES: Record<string, { section: SectionKey; anchor?: string }> = {
  model: { section: 'ai', anchor: 'ai-defaults' },
  analysis: { section: 'ai', anchor: 'ai-defaults-analysis' },
  vision: { section: 'ai', anchor: 'ai-defaults-vision' },
  speech: { section: 'ai', anchor: 'ai-defaults-transcription' },
  cover: { section: 'ai', anchor: 'ai-defaults-cover' },
}

// Calm Premium settings — left nav + setting rows (see DESIGN.md → App Layer)
const SettingsPage: React.FC = () => {
  useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const requested = new URLSearchParams(location.search).get('section') || ''
  const initialSection = useMemo<SectionKey>(() => (
    SECTION_ALIASES[requested]?.section || (NAV.find((n) => n.key === requested)?.key as SectionKey) || 'ai'
  ), [requested])
  const [active, setActive] = useState<SectionKey>(initialSection)
  const [analyticsOn, setAnalyticsOn] = useState(isAnalyticsEnabled())
  const [feedbackOpen, setFeedbackOpen] = useState(false)
  const runtime = getRuntimeInfo()
  const viewedSection = useRef<SectionKey | null>(null)
  useEffect(() => {
    if (viewedSection.current === active) return
    viewedSection.current = active
    trackExperience('settings_section_viewed', { section: active, entry_source: location.state?.settingsEntry === 'home_setup' ? 'home_setup' : 'direct' })
  }, [active])
  useEffect(() => { setActive(initialSection) }, [initialSection])
  return (
    <div className="ac-page">
      <header>
        {/* Consistent with the details page: in-page back, not relying on the top-bar logo */}
        <button className="ac-back" onClick={() => navigate('/')}>
          <Icon.Back />{t("Projects")}</button>
        <h1 className="ac-title">{t("Settings")}</h1>
        <div className="ac-meta">
          <span className="ac-mono">{runtime.version !== 'unknown' ? `v${runtime.version}` : 'dev'}</span>
          <span className="dot" />
          <span className="ac-mono">{runtime.os}/{runtime.arch}</span>
        </div>
      </header>

      <div className="ac-settings" style={{ marginTop: 36 }}>
        <nav className="ac-settings-nav" aria-label={t("Settings categories")}>
          {NAV.map((n) => (
            <button key={n.key} aria-current={active === n.key} onClick={() => navigate(`/settings?section=${n.key}`, { replace: true })}>{n.label}</button>
          ))}
        </nav>

        <div className="ac-settings-body">
          <div hidden={active !== 'ai'}>
            <AIModelSettings requestedSection={requested} requestedProvider={new URLSearchParams(location.search).get('provider') || ''} />
          </div>

          {/* ---------------- App ---------------- */}
          {active === 'app' && (
            <AppSection analyticsOn={analyticsOn} onAnalyticsChange={(on) => { setAnalyticsEnabled(on); setAnalyticsOn(on) }} />
          )}

          {active === 'publish' && <PublishSettings />}

          {/* ---------------- Feedback ---------------- */}
          {active === 'feedback' && (
            <Section title={t("Feedback")} description={t("Tell us what's wrong or what you want. The runtime environment is attached automatically, without video content or API keys. After sending, the body text and optional contact info first go to PostHog, then are published to GitHub; email addresses are not made public.")}>
              <div className="ac-rows">
                <Row label={t("Send feedback")} hint={t("Just write one sentence in the app. It is delivered via PostHog first; bugs go to GitHub Issues, ideas go to Discussions.")}>
                  <Btn variant="cta" size="sm" style={{ height: 32, fontSize: 13, padding: '0 16px' }} onClick={() => setFeedbackOpen(true)}>
                    <Icon.Chat size={13} />{t("Write feedback")}</Btn>
                </Row>
                <Row label={t("Something broke")} hint={t("The version, platform, and model are written in the Issue to make it easy to reproduce.")}>
                  <Btn size="sm" onClick={() => openExternalLink(FEEDBACK_ISSUES_URL)}>{t("Report an issue")}<Icon.External size={12} /></Btn>
                </Row>
                <Row label={t("Ideas and usage")} hint={t("Send the capabilities you hope for and how you use it to Discussions; do not open an Issue for this.")}>
                  <Btn size="sm" onClick={() => openExternalLink(FEEDBACK_DISCUSSIONS_URL)}>{t("Go to Discussions")}<Icon.External size={12} /></Btn>
                </Row>
                <Row label={t("Current status and known issues")} hint={t("Release cadence, known bugs, and workarounds are all in this pinned Issue.")}>
                  <Btn variant="text" size="sm" onClick={() => openExternalLink('https://github.com/zhouxiaoka/autoclip/issues/96')}>#96 <Icon.External size={12} /></Btn>
                </Row>
              </div>
            </Section>
          )}
        </div>
      </div>

      <FeedbackDialog open={feedbackOpen} onClose={() => setFeedbackOpen(false)} context={{ source: 'settings' }} />
    </div>
  )
}

/* ---------------- App ---------------- */
const AppSection: React.FC<{ analyticsOn: boolean; onAnalyticsChange: (on: boolean) => void }> = ({ analyticsOn, onAnalyticsChange }) => {
  useTranslation()
  const { theme, setTheme } = useTheme()
  const [autostart, setAutostart] = useState(false)
  const [keepAwake, setKeepAwake] = useState(() => isKeepAwakeEnabled())
  const [busy, setBusy] = useState(false)
  const [desktop, setDesktop] = useState(false)
  const [crashOn, setCrashOn] = useState(isCrashReportsEnabled())
  const crashPreferenceChanged = useRef(false)
  const [version, setVersion] = useState('')
  const appUpdate = useAppUpdate()

  useEffect(() => {
    let active = true
    ;(async () => {
      try {
        const isDesktop = await isDesktopMode()
        setDesktop(isDesktop)
        setVersion(await getAppVersion())
        if (isDesktop) {
          const { invoke } = await import('@tauri-apps/api/core')
          setAutostart(Boolean(await invoke('is_autostart_enabled')))
        }
        try {
          const privacy = await settingsApi.getPrivacy()
          // A delayed snapshot must not undo a newer choice or an unmounted page.
          if (active && !crashPreferenceChanged.current && typeof privacy?.crash_reports === 'boolean') {
            setCrashReportsEnabled(privacy.crash_reports)
            setCrashOn(privacy.crash_reports)
          }
        } catch {
          /* Use localStorage when the backend is not up or this is not a desktop data directory */
        }
      } catch (err) {
        console.error('Failed to check autostart status:', err)
      }
    })()
    return () => { active = false }
  }, [])

  const toggleAutostart = async (enabled: boolean) => {
    if (!desktop) { message.error(t("This feature is only available in the desktop app")); return }
    setBusy(true)
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      await invoke(enabled ? 'enable_autostart' : 'disable_autostart')
      setAutostart(enabled)
    } catch (err) {
      console.error('Failed to toggle autostart state:', err)
      message.error(t("Operation failed: {{value1}}", { value1: err }))
    } finally {
      setBusy(false)
    }
  }


  const toggleKeepAwake = (enabled: boolean) => {
    setKeepAwake(enabled)
    getKeepAwakeController().onEnabledChange(enabled)
  }

  const toggleCrashReports = async (enabled: boolean) => {
    crashPreferenceChanged.current = true
    setCrashReportsEnabled(enabled)
    setCrashOn(enabled)
    try {
      await settingsApi.updatePrivacy({ crash_reports: enabled })
    } catch {
      message.warning(t("The frontend toggle took effect, but saving the backend privacy setting failed. Please retry."))
    }
  }

  const handleCheckUpdate = async () => {
    if (!desktop && !appUpdate.preview) { message.info(t('Checking for updates is only available in the desktop app')); return }
    try {
      const result = await appUpdate.checkNow()
      if (result === 'current') {
        const current = appUpdate.currentVersion || version
        message.success(current ? t('Already on the latest version ({{version}})', { version: current }) : t('Already on the latest version'))
      }
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err)
      if (text === 'desktop-only') message.info(t('Checking for updates is only available in the desktop app'))
      else message.error(t('Failed to check for updates: {{error}}', { error: text.split('{{').join('{') }))
    }
  }

  const shownVersion = appUpdate.currentVersion || version
  const versionHint = appUpdate.phase === 'ready' || appUpdate.phase === 'restarting'
    ? t('Can update to {{version}}', { version: appUpdate.version })
    : appUpdate.phase === 'downloading'
      ? t('Preparing {{version}}', { version: appUpdate.version })
      : appUpdate.phase === 'failed'
        ? t('The update did not finish downloading: {{error}}', { error: appUpdate.error })
        : shownVersion
          ? t('Current {{version}}', { version: shownVersion })
          : t('The desktop app can check for updates on GitHub Releases.')

  return (
    <Section title={t("App")} description={t("Appearance, startup, and privacy.")}>
      <div className="ac-rows">
        <Row label={t("Appearance")} hint={t("Follows the system on first launch.")}>
          <Segmented size="sm" ariaLabel={t("Appearance")} value={theme} onChange={setTheme} options={[{ value: 'light', label: t("Light") }, { value: 'dark', label: t("Dark") }]} />
        </Row>
        <Row label={t("Launch at startup")} hint={t("When enabled, starts with the system and can be opened from the tray. Only available in the desktop app.")}>
          <Switch checked={autostart} onChange={toggleAutostart} loading={busy} disabled={!desktop} />
        </Row>
        <Row label={t("Prevent the computer from sleeping during production")} hint={t("Keeps the system awake during production (allows the screen to turn off). When off, the computer can sleep according to the system schedule.")}>
          <Switch checked={keepAwake} onChange={toggleKeepAwake} />
        </Row>
        <OutputBrandingSetting />
        <OutputCaptionsSetting />
        {(desktop || appUpdate.preview) && (
          <>
            <Row label={t('Version')} hint={versionHint}>
              {appUpdate.phase === 'ready' || appUpdate.phase === 'restarting' ? (
                <Btn size="sm" variant="cta" loading={appUpdate.phase === 'restarting'} onClick={() => void appUpdate.restart()}>{t('Update and restart')}</Btn>
              ) : appUpdate.phase === 'downloading' ? (
                <Btn size="sm" onClick={appUpdate.showToast}>{t('View progress')}</Btn>
              ) : appUpdate.phase === 'failed' ? (
                <Btn size="sm" onClick={() => void appUpdate.retry()}>{t('Retry')}</Btn>
              ) : (
                <Btn size="sm" loading={appUpdate.phase === 'checking'} onClick={() => void handleCheckUpdate()}>{t('Check for updates')}</Btn>
              )}
            </Row>
            <Row label={t('Check for updates at startup')} hint={t('Notifies you when a new version is found, at most once per day for the same version. Manual checks are still available when off.')}>
              <Switch checked={appUpdate.remindersEnabled} onChange={appUpdate.setRemindersEnabled} />
            </Row>
          </>
        )}
        <Row label={t("Anonymous usage analytics")} hint={t("Only collects anonymous events such as feature usage and final-video success / failure, without video content, subtitle text, or API keys. When off, you can still actively send feedback.")}>
          <Switch checked={analyticsOn} onChange={onAnalyticsChange} />
        </Row>
        <Row label={t("Crash reports")} hint={t("Sends crash stacks to Sentry to help with fixes. Does not include video content, subtitles, or API keys. Nothing is sent when no reporting endpoint is configured.")}>
          <Switch checked={crashOn} onChange={toggleCrashReports} />
        </Row>
      </div>
      <div style={{ marginTop: 'var(--ac-space-3xl)' }}>
        <ImportPreferenceSettings />
        {flagOverridesVisible() && <FlagOverrides />}
      </div>
    </Section>
  )
}

export default SettingsPage
