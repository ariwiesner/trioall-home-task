import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () => import('./dashboard/dashboard.component').then((m) => m.DashboardComponent),
  },
  {
    path: 'refrigerators/:id',
    loadComponent: () =>
      import('./refrigerator-detail/refrigerator-detail.component').then((m) => m.RefrigeratorDetailComponent),
  },
  {
    path: 'upload',
    loadComponent: () => import('./upload/upload.component').then((m) => m.UploadComponent),
  },
  { path: '**', redirectTo: '' },
];
