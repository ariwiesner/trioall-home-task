import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { DashboardResponse, LoggerOption, PaginatedResponse, RefrigeratorDetail, TemperatureReading, UploadResult } from './models';

// Single service for the whole (small) API surface — splitting this into
// one service per resource would just be indirection for its own sake.
@Injectable({ providedIn: 'root' })
export class ApiService {
  constructor(private http: HttpClient) {}

  getDashboard(filters: { search?: string; status?: string; branchId?: number | null }): Observable<DashboardResponse> {
    let params = new HttpParams();
    if (filters.search) params = params.set('search', filters.search);
    if (filters.status) params = params.set('status', filters.status);
    if (filters.branchId != null) params = params.set('branch_id', filters.branchId);
    return this.http.get<DashboardResponse>('/api/dashboard/', { params });
  }

  getRefrigerator(id: number): Observable<RefrigeratorDetail> {
    return this.http.get<RefrigeratorDetail>(`/api/refrigerators/${id}/`);
  }

  // Backs the detail page's warming-trend start temperature + tiny sparkline:
  // the episode itself only carries its end-of-window temperature, so the
  // readings in range are fetched to show where the trend actually started.
  getReadings(id: number, since: string, until: string): Observable<PaginatedResponse<TemperatureReading>> {
    const params = new HttpParams().set('since', since).set('until', until);
    return this.http.get<PaginatedResponse<TemperatureReading>>(`/api/refrigerators/${id}/readings/`, { params });
  }

  getLoggers(): Observable<LoggerOption[]> {
    return this.http.get<LoggerOption[]>('/api/loggers/');
  }

  uploadFile(file: File, fallbackLoggerId: number | null): Observable<UploadResult> {
    const formData = new FormData();
    formData.append('file', file);
    if (fallbackLoggerId != null) {
      formData.append('fallback_logger_id', String(fallbackLoggerId));
    }
    return this.http.post<UploadResult>('/api/uploads/', formData);
  }
}
