export const CHINA_TIME_ZONE = 'Asia/Shanghai'

type Ymd = { year: number; month: number; day: number }

function parseYmd(value?: string | null): Ymd | null {
  const match = String(value || '').trim().match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null
  if (month < 1 || month > 12 || day < 1 || day > 31) return null
  return { year, month, day }
}

function toYmdString(value: Ymd) {
  const y = String(value.year).padStart(4, '0')
  const m = String(value.month).padStart(2, '0')
  const d = String(value.day).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function formatterParts(
  value?: string | number | Date | null,
  options: Intl.DateTimeFormatOptions = {},
) {
  const source = value == null ? new Date() : new Date(value)
  if (Number.isNaN(source.getTime())) return null
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: CHINA_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    ...options,
  }).formatToParts(source)
}

function partValue(parts: Intl.DateTimeFormatPart[] | null, key: string, fallback = '0') {
  return Number(parts?.find((item) => item.type === key)?.value || fallback)
}

function chinaDateFromValue(value?: string | number | Date | null): Ymd | null {
  const parts = formatterParts(value)
  if (!parts) return null
  return {
    year: partValue(parts, 'year'),
    month: partValue(parts, 'month'),
    day: partValue(parts, 'day'),
  }
}

export function chinaDateString(value?: string | number | Date | null) {
  const ymd = chinaDateFromValue(value)
  return ymd ? toYmdString(ymd) : ''
}

export function chinaNowYearMonth() {
  const ymd = chinaDateFromValue(new Date())
  if (!ymd) return { year: 1970, month: 1 }
  return { year: ymd.year, month: ymd.month }
}

export function chinaNowMinutesOfDay(value?: string | number | Date | null) {
  const parts = formatterParts(value)
  if (!parts) return 0
  const hour = partValue(parts, 'hour')
  const minute = partValue(parts, 'minute')
  return hour * 60 + minute
}

export function parseHmToMinutes(value?: string | null) {
  const match = String(value || '').trim().match(/^(\d{2}):(\d{2})$/)
  if (!match) return null
  const hour = Number(match[1])
  const minute = Number(match[2])
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null
  return hour * 60 + minute
}

export function shiftYmdDays(dateText: string, days: number) {
  const ymd = parseYmd(dateText)
  if (!ymd) return dateText
  const ts = Date.UTC(ymd.year, ymd.month - 1, ymd.day + days)
  const next = new Date(ts)
  return toYmdString({
    year: next.getUTCFullYear(),
    month: next.getUTCMonth() + 1,
    day: next.getUTCDate(),
  })
}

export function shiftYmdMonths(dateText: string, months: number) {
  const ymd = parseYmd(dateText)
  if (!ymd) return dateText
  const firstDayTs = Date.UTC(ymd.year, ymd.month - 1 + months, 1)
  const target = new Date(firstDayTs)
  const year = target.getUTCFullYear()
  const month = target.getUTCMonth() + 1
  const monthEnd = new Date(Date.UTC(year, month, 0)).getUTCDate()
  return toYmdString({
    year,
    month,
    day: Math.min(ymd.day, monthEnd),
  })
}

export function weekRangeFromYmd(dateText: string) {
  const ymd = parseYmd(dateText)
  if (!ymd) return { start: dateText, end: dateText }
  const currentTs = Date.UTC(ymd.year, ymd.month - 1, ymd.day)
  const current = new Date(currentTs)
  const day = current.getUTCDay()
  const mondayOffset = day === 0 ? -6 : 1 - day
  const start = shiftYmdDays(toYmdString(ymd), mondayOffset)
  const end = shiftYmdDays(start, 6)
  return { start, end }
}

export function monthRangeFromYmd(dateText: string) {
  const ymd = parseYmd(dateText)
  if (!ymd) {
    return { start: dateText, end: dateText, year: 0, month: 0 }
  }
  const start = `${String(ymd.year).padStart(4, '0')}-${String(ymd.month).padStart(2, '0')}-01`
  const lastDay = new Date(Date.UTC(ymd.year, ymd.month, 0)).getUTCDate()
  const end = `${String(ymd.year).padStart(4, '0')}-${String(ymd.month).padStart(2, '0')}-${String(lastDay).padStart(2, '0')}`
  return { start, end, year: ymd.year, month: ymd.month }
}

export function dayDiffInclusive(startDate: string, endDate: string) {
  const start = parseYmd(startDate)
  const end = parseYmd(endDate)
  if (!start || !end) return 0
  const startTs = Date.UTC(start.year, start.month - 1, start.day)
  const endTs = Date.UTC(end.year, end.month - 1, end.day)
  if (endTs < startTs) return 0
  return Math.floor((endTs - startTs) / 86400000) + 1
}

export function toChinaDatetimeLocal(value?: string | number | Date | null) {
  const parts = formatterParts(value)
  if (!parts) return ''
  const year = partValue(parts, 'year')
  const month = partValue(parts, 'month')
  const day = partValue(parts, 'day')
  const hour = partValue(parts, 'hour')
  const minute = partValue(parts, 'minute')
  return `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}T${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
}

export function chinaDatetimeLocalToIso(value?: string | null) {
  const text = String(value || '').trim()
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/)
  if (!match) return null
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const hour = Number(match[4])
  const minute = Number(match[5])
  if ([year, month, day, hour, minute].some((item) => Number.isNaN(item))) return null
  const utc = Date.UTC(year, month - 1, day, hour - 8, minute, 0)
  const parsed = new Date(utc)
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString()
}

export function formatCurrency(value?: number | string | null) {
  const amount = Number(value || 0)
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: 2,
  }).format(amount)
}

export function formatDate(value?: string | null) {
  if (!value) return '-'
  const date = chinaDateString(value)
  return date || String(value).slice(0, 10)
}

export function formatDateTime(value?: string | null) {
  if (!value) return '-'
  const raw = String(value).trim()
  const normalized = raw.replace(' ', 'T')
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized)
  const parsed = new Date(hasTimezone ? normalized : `${normalized}Z`)
  if (Number.isNaN(parsed.getTime())) return raw.replace('T', ' ').slice(0, 16)
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: CHINA_TIME_ZONE,
  }).format(parsed).replace(/\//g, '-')
}

export function formatStatus(value?: string | null) {
  const map: Record<string, string> = {
    draft: '草稿',
    pending: '待审批',
    pending_approval: '待审批',
    approved: '已审批',
    paid: '已发放',
    finalized: '已发放',
    rejected: '已拒绝',
    cancelled: '已撤销',
    confirmed: '已确认',
    locked: '已锁定',
    active: '在用',
    returned: '已归还',
    in_use: '使用中',
    completed: '已完成',
    no_query: '无疑问',
    queried: '待回复',
    replied: '已回复',
    escalated: '已升级',
  }
  return map[String(value || '').toLowerCase()] || value || '-'
}

export function formatMonthNumber(month?: number | string | null) {
  if (month === undefined || month === null || month === '') return '-'
  return `${String(month).padStart(2, '0')}月`
}
