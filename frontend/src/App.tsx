import { BrowserRouter, Navigate, Route, Routes } from 'react-router'

import { AdminIndex, AdminLayout } from './features/admin/AdminLayout'
import { AuditPage } from './features/admin/AuditPage'
import { ImportPage } from './features/admin/ImportPage'
import { RoomsAdminPage } from './features/admin/RoomsAdminPage'
import { UsersAdminPage } from './features/admin/UsersAdminPage'
import { LoginPage } from './features/auth/LoginPage'
import { RequireAdmin, RequireAuth } from './features/auth/guards'
import { GridPage } from './features/grid/GridPage'
import { AppShell } from './features/shell/AppShell'

export function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  )
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<GridPage />} />
        <Route path="admin" element={<AdminLayout />}>
          <Route index element={<AdminIndex />} />
          <Route
            path="salas"
            element={
              <RequireAdmin>
                <RoomsAdminPage />
              </RequireAdmin>
            }
          />
          <Route
            path="usuarios"
            element={
              <RequireAdmin>
                <UsersAdminPage />
              </RequireAdmin>
            }
          />
          <Route
            path="importar"
            element={
              <RequireAdmin>
                <ImportPage />
              </RequireAdmin>
            }
          />
          <Route path="auditoria" element={<AuditPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
