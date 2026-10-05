import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { forkJoin, of, Subject } from 'rxjs';
import { catchError, debounceTime, distinctUntilChanged, switchMap } from 'rxjs/operators';

import { ApiService } from '../api.service';
import { DashboardBranch, DashboardSummary, FridgeStatus, RefrigeratorDetail } from '../models';
import { RelativeTimePipe } from '../relative-time.pipe';
import { buildCardReason, originalFahrenheitOf, statusMeta, StatusMeta } from '../status-copy';

interface BranchOption {
  id: number;
  name: string;
}

interface LoggerInfo {
  code: string;
}

interface FridgeCardVM {
  id: number;
  name: string;
  status: FridgeStatus;
  meta: StatusMeta;
  temperature: number | null;
  originalFahrenheit: number | null;
  lastReadingAt: string | null;
  loggerCode: string | null;
  reason: string | null;
}

interface BranchGroupVM {
  id: number;
  name: string;
  fridges: FridgeCardVM[];
}

// Good < Needs review < Problem in urgency; this ranks the OTHER way
// (lower number = more urgent) so branches/fridges sort urgent-first.
const SEVERITY_RANK: Record<FridgeStatus, number> = { problem: 0, needs_review: 1, good: 2 };

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, RelativeTimePipe],
  templateUrl: './dashboard.component.html',
  styleUrls: ['./dashboard.component.css'],
})
export class DashboardComponent implements OnInit {
  summary: DashboardSummary | null = null;
  branchGroups: BranchGroupVM[] = [];
  branchOptions: BranchOption[] = [];

  loading = true;
  error = false;

  searchTerm = '';
  statusFilter: FridgeStatus | null = null;
  branchIdFilter: number | null = null;

  private loggerInfoByFridgeKey = new Map<string, LoggerInfo>();
  private readonly searchInput$ = new Subject<string>();
  private readonly reload$ = new Subject<void>();

  constructor(private readonly api: ApiService) {}

  ngOnInit(): void {
    // Debounced so typing a search term doesn't fire a request per keystroke.
    this.searchInput$.pipe(debounceTime(300), distinctUntilChanged()).subscribe((term) => {
      this.searchTerm = term;
      this.reload$.next();
    });

    this.reload$
      .pipe(
        switchMap(() => this.loadOnce()),
      )
      .subscribe();

    // Logger→fridge lookup is small, slow-changing reference data — load it
    // once rather than refetching on every filter/search change.
    this.api.getLoggers().subscribe({
      next: (loggers) => {
        this.loggerInfoByFridgeKey.clear();
        // First match wins (mirrors the detail page's Array.find) so the two
        // pages never disagree if, e.g., a registry edit briefly leaves two
        // loggers both pointing at the same fridge.
        for (const logger of loggers) {
          if (logger.current_assignment && !this.loggerInfoByFridgeKey.has(logger.current_assignment)) {
            this.loggerInfoByFridgeKey.set(logger.current_assignment, { code: logger.external_id });
          }
        }
      },
      error: () => {
        // Non-fatal: cards just render without a logger code.
      },
    });

    this.reload$.next();
  }

  onSearchInput(value: string): void {
    this.searchInput$.next(value);
  }

  toggleStatus(status: FridgeStatus): void {
    this.statusFilter = this.statusFilter === status ? null : status;
    this.reload$.next();
  }

  onBranchChange(value: string): void {
    this.branchIdFilter = value ? Number(value) : null;
    this.reload$.next();
  }

  clearFilters(): void {
    this.searchTerm = '';
    this.statusFilter = null;
    this.branchIdFilter = null;
    this.reload$.next();
  }

  refresh(): void {
    this.reload$.next();
  }

  get hasActiveFilters(): boolean {
    return !!this.searchTerm || !!this.statusFilter || this.branchIdFilter != null;
  }

  private loadOnce() {
    this.loading = true;
    this.error = false;
    const isUnfiltered = !this.hasActiveFilters;

    return this.api
      .getDashboard({
        search: this.searchTerm || undefined,
        status: this.statusFilter ?? undefined,
        branchId: this.branchIdFilter,
      })
      .pipe(
        switchMap((response) => {
          this.summary = response.summary;
          if (isUnfiltered) {
            this.branchOptions = response.branches.map((b) => ({ id: b.id, name: b.name }));
          }
          return this.enrich(response.branches);
        }),
        catchError(() => {
          this.error = true;
          return of(null);
        }),
      )
      .pipe(
        switchMap((groups) => {
          if (groups) {
            this.branchGroups = groups;
          }
          this.loading = false;
          return of(undefined);
        }),
      );
  }

  // Dashboard's own list endpoint only has {id, name, status, last_reading_at}
  // per fridge — temperature and a plain-language reason aren't in that
  // response. The per-fridge detail endpoint already has both, so it's
  // fetched in parallel for every fridge currently shown (small bakery-scale
  // lists — see plan) rather than changing the dashboard API.
  private enrich(branches: DashboardBranch[]) {
    const allFridges = branches.flatMap((b) => b.refrigerators.map((f) => ({ branch: b, fridge: f })));
    if (allFridges.length === 0) {
      return of([] as BranchGroupVM[]);
    }

    return forkJoin(
      allFridges.map(({ fridge }) =>
        this.api.getRefrigerator(fridge.id).pipe(
          catchError(() => of(null)),
        ),
      ),
    ).pipe(
      switchMap((details) => {
        const byBranch = new Map<number, BranchGroupVM>();
        allFridges.forEach(({ branch, fridge }, i) => {
          const detail = details[i];
          const key = `${branch.name} / ${fridge.name}`;
          const loggerInfo = this.loggerInfoByFridgeKey.get(key);
          const temperature = detail?.last_temperature_c ?? null;
          const card: FridgeCardVM = {
            id: fridge.id,
            name: fridge.name,
            status: fridge.status,
            meta: statusMeta(fridge.status),
            temperature,
            originalFahrenheit: detail
              ? originalFahrenheitOf(detail.last_reading_temperature_unit, detail.last_reading_raw_temperature)
              : null,
            lastReadingAt: fridge.last_reading_at,
            loggerCode: loggerInfo?.code ?? null,
            reason: detail ? buildCardReason(detail as RefrigeratorDetail) : null,
          };
          if (!byBranch.has(branch.id)) {
            byBranch.set(branch.id, { id: branch.id, name: branch.name, fridges: [] });
          }
          byBranch.get(branch.id)!.fridges.push(card);
        });

        const groups = Array.from(byBranch.values());
        for (const group of groups) {
          group.fridges.sort((a, b) => SEVERITY_RANK[a.status] - SEVERITY_RANK[b.status]);
        }
        groups.sort((a, b) => this.branchRank(a) - this.branchRank(b));
        return of(groups);
      }),
    );
  }

  private branchRank(group: BranchGroupVM): number {
    return Math.min(...group.fridges.map((f) => SEVERITY_RANK[f.status]));
  }
}
