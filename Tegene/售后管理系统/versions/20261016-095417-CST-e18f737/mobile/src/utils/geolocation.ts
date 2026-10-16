import { Capacitor } from '@capacitor/core'
import { Geolocation, type CallbackID, type Position, type PositionOptions } from '@capacitor/geolocation'

export interface MobileGeoPosition {
  timestamp: number
  provider: 'native_geolocation' | 'browser_geolocation'
  coords: {
    latitude: number
    longitude: number
    accuracy: number | null
  }
}

export type MobileGeoWatchId =
  | { provider: 'native_geolocation'; id: CallbackID }
  | { provider: 'browser_geolocation'; id: number }

type PositionSuccess = (position: MobileGeoPosition) => void
type PositionFailure = (error: unknown) => void

const isNative = Capacitor.getPlatform() !== 'web'

export function isMobileGeolocationSupported() {
  return isNative || (typeof navigator !== 'undefined' && Boolean(navigator.geolocation))
}

export function getMobileGeolocationErrorMessage(error: unknown, fallback = '定位失败') {
  if (error instanceof Error && error.message) return error.message
  if (typeof error === 'string' && error.trim()) return error.trim()
  if (error && typeof error === 'object' && 'message' in error) {
    const message = String((error as { message?: unknown }).message || '').trim()
    if (message) return message
  }
  return fallback
}

function normalizeNativePosition(position: Position): MobileGeoPosition {
  return {
    timestamp: position.timestamp,
    provider: 'native_geolocation',
    coords: {
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
      accuracy: Number.isFinite(Number(position.coords.accuracy)) ? Number(position.coords.accuracy) : null,
    },
  }
}

function normalizeBrowserPosition(position: GeolocationPosition): MobileGeoPosition {
  return {
    timestamp: position.timestamp,
    provider: 'browser_geolocation',
    coords: {
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
      accuracy: Number.isFinite(Number(position.coords.accuracy)) ? Number(position.coords.accuracy) : null,
    },
  }
}

async function ensureNativeLocationPermission() {
  const status = await Geolocation.checkPermissions()
  if (status.location === 'granted' || status.coarseLocation === 'granted') return

  const requested = await Geolocation.requestPermissions({ permissions: ['location'] })
  if (requested.location === 'granted' || requested.coarseLocation === 'granted') return

  throw new Error('未授予定位权限')
}

function getBrowserCurrentPosition(options: PositionOptions) {
  return new Promise<MobileGeoPosition>((resolve, reject) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      reject(new Error('当前设备不支持定位'))
      return
    }
    navigator.geolocation.getCurrentPosition(
      (position) => resolve(normalizeBrowserPosition(position)),
      reject,
      options,
    )
  })
}

export async function getCurrentMobilePosition(options: PositionOptions = {}) {
  if (isNative) {
    await ensureNativeLocationPermission()
    const position = await Geolocation.getCurrentPosition(options)
    return normalizeNativePosition(position)
  }

  return getBrowserCurrentPosition(options)
}

export async function watchMobilePosition(
  options: PositionOptions,
  onSuccess: PositionSuccess,
  onError: PositionFailure,
): Promise<MobileGeoWatchId> {
  if (isNative) {
    await ensureNativeLocationPermission()
    const id = await Geolocation.watchPosition(options, (position, error) => {
      if (error) {
        onError(error)
        return
      }
      if (position) onSuccess(normalizeNativePosition(position))
    })
    return { provider: 'native_geolocation', id }
  }

  if (typeof navigator === 'undefined' || !navigator.geolocation) {
    throw new Error('当前设备不支持定位')
  }

  const id = navigator.geolocation.watchPosition(
    (position) => onSuccess(normalizeBrowserPosition(position)),
    onError,
    options,
  )
  return { provider: 'browser_geolocation', id }
}

export async function clearMobilePositionWatch(watchId: MobileGeoWatchId | null) {
  if (!watchId) return
  if (watchId.provider === 'native_geolocation') {
    await Geolocation.clearWatch({ id: watchId.id })
    return
  }
  if (typeof navigator !== 'undefined' && navigator.geolocation) {
    navigator.geolocation.clearWatch(watchId.id)
  }
}
