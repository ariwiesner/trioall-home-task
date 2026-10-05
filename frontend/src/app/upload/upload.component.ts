import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { ApiService } from '../api.service';
import { LoggerOption, UploadResult } from '../models';

@Component({
  selector: 'app-upload',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './upload.component.html',
  styleUrls: ['./upload.component.css'],
})
export class UploadComponent implements OnInit {
  loggers: LoggerOption[] = [];
  selectedFile: File | null = null;
  fallbackLoggerId: number | null = null;

  uploading = false;
  result: UploadResult | null = null;
  errorMessage: string | null = null;

  constructor(private readonly api: ApiService) {}

  ngOnInit(): void {
    this.api.getLoggers().subscribe({
      next: (loggers) => (this.loggers = loggers),
      error: () => {
        // Non-fatal: the fallback-logger dropdown just stays empty. A file
        // with its own Logger column still uploads fine without it.
      },
    });
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.selectedFile = input.files && input.files.length > 0 ? input.files[0] : null;
    this.result = null;
    this.errorMessage = null;
  }

  upload(): void {
    if (!this.selectedFile) {
      return;
    }
    this.uploading = true;
    this.result = null;
    this.errorMessage = null;

    this.api.uploadFile(this.selectedFile, this.fallbackLoggerId).subscribe({
      next: (result) => {
        this.result = result;
        this.uploading = false;
      },
      error: (err: HttpErrorResponse) => {
        this.errorMessage = err.error?.detail || 'Upload failed. Please try again.';
        this.uploading = false;
      },
    });
  }

  reset(): void {
    this.selectedFile = null;
    this.fallbackLoggerId = null;
    this.result = null;
    this.errorMessage = null;
  }
}
