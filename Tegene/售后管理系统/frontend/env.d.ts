/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ENABLE_CONSOLE_MONITOR?: string
  readonly VITE_CONSOLE_COLLECTOR_URL?: string
  readonly VITE_CONSOLE_MONITOR_INCLUDE_WARN?: string
  readonly VITE_CONSOLE_MONITOR_MAX_PER_MINUTE?: string
  readonly VITE_CONSOLE_MONITOR_LOCAL_DEDUPE_MS?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}
