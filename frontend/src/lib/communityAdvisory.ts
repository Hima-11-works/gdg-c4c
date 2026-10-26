import type { AdvisoryContextOut } from './types'

export type CommunityLanguage = 'en' | 'hi' | 'pa'

const LOCALE: Record<CommunityLanguage, string> = {
  en: 'en-IN',
  hi: 'hi-IN',
  pa: 'pa-IN',
}

const LANGUAGE_LABEL: Record<CommunityLanguage, string> = {
  en: 'English',
  hi: 'हिन्दी',
  pa: 'ਪੰਜਾਬੀ',
}

function formatForecastTime(value: string, language: CommunityLanguage): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'unreadable time'
  return `${new Intl.DateTimeFormat(LOCALE[language], {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Asia/Kolkata',
  }).format(date)} IST`
}

function formatHours(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

function qualityNotes(context: AdvisoryContextOut, language: CommunityLanguage): string[] {
  if (language === 'hi') {
    return [
      `डेटा मोड: ${context.mode}`,
      ...(context.synthetic ? ['इसमें कृत्रिम इनपुट शामिल हैं'] : []),
      ...(context.fallback ? ['इसमें वैकल्पिक स्रोतों के इनपुट शामिल हैं'] : []),
      ...(context.stale ? ['कुछ इनपुट पुराने हैं'] : []),
    ]
  }
  if (language === 'pa') {
    return [
      `ਡਾਟਾ ਮੋਡ: ${context.mode}`,
      ...(context.synthetic ? ['ਇਸ ਵਿੱਚ ਸਿੰਥੈਟਿਕ ਇਨਪੁੱਟ ਸ਼ਾਮਲ ਹਨ'] : []),
      ...(context.fallback ? ['ਇਸ ਵਿੱਚ ਫਾਲਬੈਕ ਸਰੋਤਾਂ ਦੇ ਇਨਪੁੱਟ ਸ਼ਾਮਲ ਹਨ'] : []),
      ...(context.stale ? ['ਕੁਝ ਇਨਪੁੱਟ ਪੁਰਾਣੇ ਹਨ'] : []),
    ]
  }
  return [
    `Data mode: ${context.mode}`,
    ...(context.synthetic ? ['Includes synthetic inputs'] : []),
    ...(context.fallback ? ['Includes fallback-source inputs'] : []),
    ...(context.stale ? ['Some inputs are stale'] : []),
  ]
}

/** A deterministic community message built only from one saved alert context. */
export function buildCommunityAdvisory(
  context: AdvisoryContextOut,
  language: CommunityLanguage,
): string {
  const region = context.region
  const time = formatForecastTime(context.forecast_at, language)
  const hours = formatHours(context.forecast_hours)
  const pm25 = context.forecast_pm25.toFixed(1)
  const severity =
    language === 'hi'
      ? context.severity === 'critical'
        ? 'गंभीर चेतावनी'
        : 'चेतावनी'
      : language === 'pa'
        ? context.severity === 'critical'
          ? 'ਗੰਭੀਰ ਚੇਤਾਵਨੀ'
          : 'ਚੇਤਾਵਨੀ'
        : context.severity
  const quality = qualityNotes(context, language).join('। ')

  if (language === 'hi') {
    return [
      `${region} के लिए प्रकाशित ${severity}।`,
      `पूर्वानुमान के अनुसार ${time} के आसपास (लगभग ${hours} घंटे बाद) PM2.5 स्तर ${pm25} µg/m³ हो सकता है।`,
      'यह मॉडल का पूर्वानुमान है; इससे प्रदूषण के स्रोत या किसी घटना की पुष्टि नहीं होती।',
      `${quality}।`,
      'कार्रवाई से पहले स्थानीय प्रशासन की आधिकारिक जानकारी देखें।',
    ].join(' ')
  }

  if (language === 'pa') {
    return [
      `${region} ਲਈ ਪ੍ਰਕਾਸ਼ਿਤ ${severity}।`,
      `ਅਨੁਮਾਨ ਅਨੁਸਾਰ ${time} ਦੇ ਆਸ-ਪਾਸ (ਲਗਭਗ ${hours} ਘੰਟਿਆਂ ਬਾਅਦ) PM2.5 ਪੱਧਰ ${pm25} µg/m³ ਹੋ ਸਕਦਾ ਹੈ।`,
      'ਇਹ ਮਾਡਲ ਦਾ ਪੂਰਵ-ਅਨੁਮਾਨ ਹੈ; ਇਸ ਨਾਲ ਪ੍ਰਦੂਸ਼ਣ ਦੇ ਸਰੋਤ ਜਾਂ ਕਿਸੇ ਘਟਨਾ ਦੀ ਪੁਸ਼ਟੀ ਨਹੀਂ ਹੁੰਦੀ।',
      `${quality}।`,
      'ਕਾਰਵਾਈ ਤੋਂ ਪਹਿਲਾਂ ਸਥਾਨਕ ਪ੍ਰਸ਼ਾਸਨ ਦੀ ਅਧਿਕਾਰਕ ਜਾਣਕਾਰੀ ਵੇਖੋ।',
    ].join(' ')
  }

  return [
    `Published ${severity} forecast for ${region}.`,
    `The forecast estimates PM2.5 at ${pm25} µg/m³ around ${time} (about ${hours} hours ahead).`,
    'This is a model forecast; it does not confirm a pollution source or event.',
    `${quality}.`,
    'Check current readings and official local updates before acting.',
  ].join(' ')
}

export function communityLanguageLabel(language: CommunityLanguage): string {
  return LANGUAGE_LABEL[language]
}

export function communityReviewLabel(language: CommunityLanguage): string | null {
  if (language === 'hi') return 'Hindi language draft · not reviewed by a fluent speaker'
  if (language === 'pa') return 'Punjabi language draft · not reviewed by a fluent speaker'
  return null
}

export function communityLocale(language: CommunityLanguage): string {
  return LOCALE[language]
}
