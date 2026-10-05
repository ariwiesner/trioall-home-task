export type FridgeStatus = 'good' | 'problem' | 'needs_review';

export interface DashboardSummary {
  good: number;
  problem: number;
  needs_review: number;
}

export interface DashboardRefrigerator {
  id: number;
  name: string;
  status: FridgeStatus;
  last_reading_at: string | null;
}

export interface DashboardBranch {
  id: number;
  name: string;
  refrigerators: DashboardRefrigerator[];
}

export interface DashboardResponse {
  summary: DashboardSummary;
  branches: DashboardBranch[];
}

export interface Episode {
  start: string;
  end: string;
  duration_minutes: number;
  max_temp: number | null;
}

export type GapKind = 'never_reported' | 'stopped_reporting' | null;

export interface RefrigeratorDetail {
  id: number;
  name: string;
  branch: string;
  status: FridgeStatus;
  last_reading_at: string | null;
  last_temperature_c: number | null;
  // The same reading's original stored text value + unit — not guessed
  // from the logger currently assigned to this fridge.
  last_reading_raw_temperature: string | null;
  last_reading_temperature_unit: 'C' | 'F' | '';
  active_breach: boolean;
  active_gap: boolean;
  active_gap_kind: GapKind;
  active_warming: boolean;
  breach_episodes: Episode[];
  gap_episodes: Episode[];
  warming_episodes: Episode[];
}

export interface TemperatureReading {
  timestamp: string;
  temperature_c: number | null;
  raw_temperature: string | null;
  temperature_unit: 'C' | 'F' | '';
  is_valid: boolean;
  reason: string;
  source_file: string;
}

export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface LoggerOption {
  id: number;
  external_id: string;
  unit: 'C' | 'F';
  current_assignment: string | null;
}

export interface UploadResult {
  id: number;
  original_filename: string;
  uploaded_at: string;
  fallback_logger: number | null;
  row_count: number;
  valid_count: number;
  invalid_count: number;
  duplicate_count: number;
  unresolved_count: number;
  unknown_logger_codes: string[];
}
