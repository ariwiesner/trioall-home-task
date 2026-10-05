import { FridgeStatus, RefrigeratorDetail } from './models';

// Plain-language copy and formatting shared by the dashboard and the
// refrigerator detail page, so both present the same status the same way.
// Nothing here reinterprets the backend's status/threshold logic — it only
// turns the fields it already returns into human sentences.

export interface StatusMeta {
  icon: string;
  label: string;
  colorClass: string;
}

const STATUS_META: Record<FridgeStatus, StatusMeta> = {
  good: { icon: '🟢', label: 'Good', colorClass: 'status-good' },
  problem: { icon: '🔴', label: 'Problem', colorClass: 'status-problem' },
  needs_review: { icon: '🟡', label: 'Needs review', colorClass: 'status-needs_review' },
};

export function statusMeta(status: FridgeStatus): StatusMeta {
  return STATUS_META[status];
}

// Only a Fahrenheit reading has an "original" value worth showing — the raw
// text is read from the reading's own stored temperature_unit/raw_temperature
// (never guessed from whichever logger is currently assigned to the fridge,
// since that can change independently of what recorded a given reading).
export function originalFahrenheitOf(unit: 'C' | 'F' | '', raw: string | null): number | null {
  return unit === 'F' && raw !== null ? parseFloat(raw) : null;
}

/** "45m", "1h", "1h 15m" — never raw minute counts. */
export function formatDuration(minutes: number | null | undefined): string {
  if (minutes == null || !isFinite(minutes)) {
    return '';
  }
  const total = Math.max(1, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  if (hours === 0) {
    return `${mins}m`;
  }
  return mins === 0 ? `${hours}h` : `${hours}h ${mins}m`;
}

function dayLabel(date: Date): string {
  const now = new Date();
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diffDays = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function timeLabel(date: Date): string {
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
}

/** "Today · 10:00–10:45" / "Yesterday · 08:45–10:00" / "Oct 4 · 08:45–10:00" */
export function formatEventRange(start: string, end: string | null): string {
  const s = new Date(start);
  const label = `${dayLabel(s)} · ${timeLabel(s)}`;
  if (!end) {
    return label;
  }
  return `${label}–${timeLabel(new Date(end))}`;
}

export function formatDateBucket(date: Date): string {
  return dayLabel(date);
}

/** One short line for a dashboard card — only shown when status isn't Good. */
export function buildCardReason(detail: {
  active_breach: boolean;
  active_gap: boolean;
  active_gap_kind: string | null;
  active_warming: boolean;
  breach_episodes: { duration_minutes: number }[];
  last_reading_at: string | null;
}): string | null {
  if (detail.active_breach) {
    const episode = detail.breach_episodes[detail.breach_episodes.length - 1];
    const duration = episode ? formatDuration(episode.duration_minutes) : '';
    return duration ? `Temperature above 5°C for ${duration}` : 'Temperature is above 5°C';
  }
  if (detail.active_gap) {
    if (detail.active_gap_kind === 'never_reported') {
      return 'No readings received yet';
    }
    return detail.last_reading_at
      ? `No reading for ${formatDuration((Date.now() - new Date(detail.last_reading_at).getTime()) / 60000)}`
      : 'Not currently receiving readings';
  }
  if (detail.active_warming) {
    return 'Temperature has been rising consistently';
  }
  return null;
}

/** A fuller sentence for the detail page's "Current status" section. */
export function buildCurrentStatusText(detail: Pick<
  RefrigeratorDetail,
  'active_breach' | 'active_gap' | 'active_gap_kind' | 'active_warming' | 'breach_episodes'
>): string {
  if (detail.active_breach) {
    const episode = detail.breach_episodes[detail.breach_episodes.length - 1];
    const duration = episode ? formatDuration(episode.duration_minutes) : '';
    return duration
      ? `Temperature has been above 5°C for ${duration}.`
      : 'Temperature is currently above 5°C.';
  }
  if (detail.active_gap) {
    if (detail.active_gap_kind === 'stopped_reporting') {
      const warmingNote = detail.active_warming ? ' A warming trend was also detected before it stopped.' : '';
      return `This refrigerator isn't currently sending readings.${warmingNote}`;
    }
  }
  if (detail.active_warming) {
    return 'Temperature is currently safe, but a warming trend was detected.';
  }
  return 'No current issues.';
}
