import { Pipe, PipeTransform } from '@angular/core';

// Used on both the dashboard cards and the refrigerator detail page, so a
// shared pipe avoids duplicating the same formatting logic in two places.
@Pipe({ name: 'relativeTime', standalone: true })
export class RelativeTimePipe implements PipeTransform {
  transform(value: string | null): string {
    if (!value) {
      return 'Never';
    }
    const diffMs = Date.now() - new Date(value).getTime();
    const diffMin = Math.round(diffMs / 60000);
    if (diffMin < 1) {
      return 'Just now';
    }
    if (diffMin < 60) {
      return `${diffMin}m ago`;
    }
    const diffHours = Math.round(diffMin / 60);
    if (diffHours < 24) {
      return `${diffHours}h ago`;
    }
    const diffDays = Math.round(diffHours / 24);
    return `${diffDays}d ago`;
  }
}
