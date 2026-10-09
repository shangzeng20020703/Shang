import type { CapacitorConfig } from '@capacitor/cli'

function csvEnv(name: string) {
  return String(process.env[name] || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

const allowNavigation = csvEnv('CAPACITOR_ALLOW_NAVIGATION')
const cleartext = process.env.CAPACITOR_CLEAR_TEXT === 'true'
const allowMixedContent = process.env.CAPACITOR_ANDROID_ALLOW_MIXED_CONTENT === 'true'

const config: CapacitorConfig = {
  appId: 'com.aftersales.fieldmanagement',
  appName: '售后管理系统',
  webDir: 'dist',
}

if (allowMixedContent) {
  config.android = {
    allowMixedContent: true,
  }
}

if (cleartext || allowNavigation.length > 0) {
  config.server = {
    ...(cleartext ? { cleartext: true } : {}),
    ...(allowNavigation.length > 0 ? { allowNavigation } : {}),
  }
}

export default config
