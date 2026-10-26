import type { CommunityLanguage } from './communityAdvisory'

const SPEECH_LOCALE: Record<CommunityLanguage, string> = {
  en: 'en-IN',
  hi: 'hi-IN',
  pa: 'pa-IN',
}

let activeUtterance: SpeechSynthesisUtterance | null = null

function speechEngine(): SpeechSynthesis | null {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return null
  if (typeof SpeechSynthesisUtterance === 'undefined') return null
  return window.speechSynthesis
}

export function supportsSpeechPreview(): boolean {
  return speechEngine() !== null
}

/** Return null when this browser has no speech synthesis API. */
export function readSpeechVoices(): SpeechSynthesisVoice[] | null {
  return speechEngine()?.getVoices() ?? null
}

/** Subscribe to late-loaded browser voices and immediately publish the current list. */
export function subscribeToSpeechVoices(
  onChange: (voices: SpeechSynthesisVoice[]) => void,
): () => void {
  const engine = speechEngine()
  if (engine === null) return () => undefined

  const handleVoicesChanged = () => onChange(engine.getVoices())
  engine.addEventListener('voiceschanged', handleVoicesChanged)

  return () => engine.removeEventListener('voiceschanged', handleVoicesChanged)
}

/** Match only the requested language; never fall back to an unrelated voice. */
export function matchingSpeechVoice(
  voices: SpeechSynthesisVoice[],
  language: CommunityLanguage,
): SpeechSynthesisVoice | null {
  const languageTag = SPEECH_LOCALE[language].slice(0, 2)
  const matching = voices.filter((voice) => {
    const locale = voice.lang.replaceAll('_', '-').toLowerCase()
    return locale === languageTag || locale.startsWith(`${languageTag}-`)
  })
  return (
    matching.find(
      (voice) => voice.lang.replaceAll('_', '-').toLowerCase() === SPEECH_LOCALE[language],
    ) ??
    matching[0] ??
    null
  )
}

export function stopSpeechPreview(): void {
  const utterance = activeUtterance
  activeUtterance = null
  if (utterance !== null) {
    utterance.onend = null
    utterance.onerror = null
  }
  speechEngine()?.cancel()
}

/** Start speech only with a voice matching the selected language. */
export function playSpeechPreview(
  text: string,
  language: CommunityLanguage,
  voice: SpeechSynthesisVoice | null,
  onFinish: (error?: string) => void,
): boolean {
  const engine = speechEngine()
  if (engine === null || voice === null || matchingSpeechVoice([voice], language) === null) {
    return false
  }

  stopSpeechPreview()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = SPEECH_LOCALE[language]
  utterance.voice = voice
  utterance.onend = () => finish()
  utterance.onerror = (event) => {
    finish(event.error === 'canceled' ? undefined : 'The browser could not play this preview.')
  }

  function finish(error?: string) {
    if (activeUtterance !== utterance) return
    activeUtterance = null
    onFinish(error)
  }

  activeUtterance = utterance
  try {
    engine.speak(utterance)
    return true
  } catch {
    finish('The browser could not start speech playback.')
    return false
  }
}
