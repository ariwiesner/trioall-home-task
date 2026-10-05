import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { forkJoin, of } from 'rxjs';
import { catchError, switchMap } from 'rxjs/operators';

import { ApiService } from '../api.service';
import { Episode, RefrigeratorDetail } from '../models';
import { RelativeTimePipe } from '../relative-time.pipe';
import {
  buildCurrentStatusText,
  formatDateBucket,
  formatDuration,
  formatEventRange,
  originalFahrenheitOf,
  statusMeta,
  StatusMeta,
} from '../status-copy';

type EventKind = 'breach' | 'warming' | 'gap' | 'current_gap';

interface HistoryEventVM {
  kind: EventKind;
  start: string;
  end: string | null;
  isCurrent: boolean;
  icon: string;
  title: string;
  rangeLabel: string;
  durationLabel: string;
  maxTemp?: number | null;
  maxTempF?: number | null;
  startTemp?: number | null;
  startTempF?: number | null;
  sparklinePoints?: string;
  sourceFile?: string | null;
  statusPillIcon: string;
  statusPillLabel: string;
}

interface ReadingLite {
  timestamp: string;
  temperature_c: number | null;
  raw_temperature: string | null;
  temperature_unit: 'C' | 'F' | '';
  is_valid: boolean;
  source_file: string;
}

interface DateGroupVM {
  label: string;
  events: HistoryEventVM[];
}

@Component({
  selector: 'app-refrigerator-detail',
  standalone: true,
  imports: [CommonModule, RouterLink, RelativeTimePipe],
  templateUrl: './refrigerator-detail.component.html',
  styleUrls: ['./refrigerator-detail.component.css'],
})
export class RefrigeratorDetailComponent implements OnInit {
  fridge: RefrigeratorDetail | null = null;
  meta: StatusMeta | null = null;
  loggerCode: string | null = null;
  heroOriginalF: number | null = null;
  heroSourceFile: string | null = null;
  currentStatusText = '';
  neverReported = false;
  dateGroups: DateGroupVM[] = [];
  hasHistory = false;

  loading = true;
  error = false;

  constructor(private readonly route: ActivatedRoute, private readonly api: ApiService) {}

  ngOnInit(): void {
    const id = Number(this.route.snapshot.paramMap.get('id'));
    this.load(id);
  }

  private load(id: number): void {
    this.loading = true;
    this.error = false;

    this.api
      .getRefrigerator(id)
      .pipe(
        switchMap((fridge) =>
          forkJoin({
            fridge: of(fridge),
            loggers: this.api.getLoggers().pipe(catchError(() => of([]))),
            episodeReadings: this.loadEpisodeReadings(id, fridge),
          }),
        ),
      )
      .subscribe({
        next: ({ fridge, loggers, episodeReadings }) => {
          this.fridge = fridge;
          this.meta = statusMeta(fridge.status);
          const key = `${fridge.branch} / ${fridge.name}`;
          const logger = loggers.find((l) => l.current_assignment === key);
          this.loggerCode = logger?.external_id ?? null;
          this.heroOriginalF = originalFahrenheitOf(
            fridge.last_reading_temperature_unit,
            fridge.last_reading_raw_temperature,
          );
          const latestReadings = episodeReadings['latest'] ?? [];
          this.heroSourceFile = latestReadings.length > 0 ? latestReadings[latestReadings.length - 1].source_file : null;

          this.neverReported = fridge.active_gap_kind === 'never_reported';
          if (!this.neverReported) {
            this.currentStatusText = buildCurrentStatusText(fridge);
            this.dateGroups = this.buildDateGroups(fridge, episodeReadings);
            this.hasHistory = this.dateGroups.length > 0;
          }
          this.loading = false;
        },
        error: () => {
          this.error = true;
          this.loading = false;
        },
      });
  }

  // Breach/warming episodes only carry a max temperature, no per-reading
  // detail — the raw readings in range (an endpoint the backend already
  // exposes, previously unused by the frontend) supply the warming start
  // temperature, the sparkline points, and each event's source file. Also
  // covers a single-point range for the hero temp's source file.
  private loadEpisodeReadings(id: number, fridge: RefrigeratorDetail) {
    const ranges: { key: string; start: string; end: string }[] = [
      ...fridge.warming_episodes.map((e) => ({ key: `warming:${e.start}`, start: e.start, end: e.end })),
      ...fridge.breach_episodes.map((e) => ({ key: `breach:${e.start}`, start: e.start, end: e.end })),
    ];
    if (fridge.last_reading_at) {
      ranges.push({ key: 'latest', start: fridge.last_reading_at, end: fridge.last_reading_at });
    }
    if (ranges.length === 0) {
      return of<Record<string, ReadingLite[]>>({});
    }
    const requests = ranges.map((r) =>
      this.api.getReadings(id, r.start, r.end).pipe(
        catchError(() => of({ count: 0, next: null, previous: null, results: [] as ReadingLite[] })),
      ),
    );
    return forkJoin(requests).pipe(
      switchMap((responses) => {
        const byKey: Record<string, ReadingLite[]> = {};
        ranges.forEach((r, i) => {
          byKey[r.key] = responses[i].results.filter((x) => x.is_valid && x.temperature_c !== null);
        });
        return of(byKey);
      }),
    );
  }

  private buildDateGroups(fridge: RefrigeratorDetail, episodeReadings: Record<string, ReadingLite[]>): DateGroupVM[] {
    const events: HistoryEventVM[] = [];

    fridge.breach_episodes.forEach((episode, i, arr) => {
      const isCurrent = i === arr.length - 1 && fridge.active_breach;
      events.push(this.breachEvent(episode, isCurrent, episodeReadings[`breach:${episode.start}`] ?? []));
    });

    fridge.warming_episodes.forEach((episode, i, arr) => {
      const isCurrent = i === arr.length - 1 && fridge.active_warming;
      events.push(this.warmingEvent(episode, isCurrent, episodeReadings[`warming:${episode.start}`] ?? []));
    });

    fridge.gap_episodes.forEach((episode) => {
      events.push(this.gapEvent(episode, false));
    });

    if (fridge.active_gap && fridge.active_gap_kind === 'stopped_reporting' && fridge.last_reading_at) {
      const minutes = (Date.now() - new Date(fridge.last_reading_at).getTime()) / 60000;
      events.push(
        this.gapEvent({ start: fridge.last_reading_at, end: fridge.last_reading_at, duration_minutes: minutes, max_temp: null }, true),
      );
    }

    events.sort((a, b) => new Date(b.start).getTime() - new Date(a.start).getTime());

    const groups: DateGroupVM[] = [];
    for (const event of events) {
      const label = formatDateBucket(new Date(event.start));
      const lastGroup = groups[groups.length - 1];
      if (lastGroup && lastGroup.label === label) {
        lastGroup.events.push(event);
      } else {
        groups.push({ label, events: [event] });
      }
    }
    return groups;
  }

  private breachEvent(episode: Episode, isCurrent: boolean, readings: ReadingLite[]): HistoryEventVM {
    // The peak reading is the one whose temperature matches the episode's
    // max_temp — that's the one worth attributing a source file to.
    const peakReading = readings.find((r) => r.temperature_c === episode.max_temp);
    return {
      kind: 'breach',
      start: episode.start,
      end: episode.end,
      isCurrent,
      icon: '🔴',
      title: 'Temperature breach',
      rangeLabel: formatEventRange(episode.start, episode.end),
      durationLabel: formatDuration(episode.duration_minutes),
      maxTemp: episode.max_temp,
      maxTempF: peakReading
        ? originalFahrenheitOf(peakReading.temperature_unit, peakReading.raw_temperature)
        : null,
      sourceFile: peakReading?.source_file ?? null,
      statusPillIcon: isCurrent ? '🔴' : '⚪',
      statusPillLabel: isCurrent ? 'Problem' : 'Resolved',
    };
  }

  private warmingEvent(episode: Episode, isCurrent: boolean, readings: ReadingLite[]): HistoryEventVM {
    const startReading = readings.length > 0 ? readings[0] : null;
    // The end-of-window reading — the one max_temp actually describes.
    const endReading = readings.length > 0 ? readings[readings.length - 1] : null;
    return {
      kind: 'warming',
      start: episode.start,
      end: episode.end,
      isCurrent,
      icon: '🟡',
      title: 'Warming trend',
      rangeLabel: formatEventRange(episode.start, episode.end),
      durationLabel: formatDuration(episode.duration_minutes),
      startTemp: startReading?.temperature_c ?? null,
      startTempF: startReading
        ? originalFahrenheitOf(startReading.temperature_unit, startReading.raw_temperature)
        : null,
      maxTemp: episode.max_temp,
      maxTempF: endReading
        ? originalFahrenheitOf(endReading.temperature_unit, endReading.raw_temperature)
        : null,
      sparklinePoints: this.buildSparkline(readings),
      sourceFile: endReading?.source_file ?? null,
      statusPillIcon: isCurrent ? '🟡' : '⚪',
      statusPillLabel: isCurrent ? 'Review recommended' : 'Resolved',
    };
  }

  private gapEvent(episode: Episode, isCurrent: boolean): HistoryEventVM {
    return {
      kind: isCurrent ? 'current_gap' : 'gap',
      start: episode.start,
      end: isCurrent ? null : episode.end,
      isCurrent,
      icon: isCurrent ? '🔴' : '⚪',
      title: 'Data gap',
      rangeLabel: formatEventRange(episode.start, isCurrent ? null : episode.end),
      durationLabel: formatDuration(episode.duration_minutes),
      statusPillIcon: isCurrent ? '🔴' : '⚪',
      statusPillLabel: isCurrent ? 'Currently not receiving readings' : 'Resolved',
    };
  }

  private buildSparkline(readings: { temperature_c: number | null }[]): string | undefined {
    const temps = readings.map((r) => r.temperature_c).filter((t): t is number => t !== null);
    if (temps.length < 2) {
      return undefined;
    }
    const width = 100;
    const height = 28;
    const min = Math.min(...temps);
    const max = Math.max(...temps);
    const range = max - min || 1;
    return temps
      .map((t, i) => {
        const x = (i / (temps.length - 1)) * width;
        const y = height - ((t - min) / range) * height;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  }
}
