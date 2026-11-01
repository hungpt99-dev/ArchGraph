import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Spinner, ToastProvider } from "./components/ui";
import { AuthProvider, useAuth } from "./lib/auth";
import { AI } from "./pages/AI";
import { Docs } from "./pages/Docs";
import { Events } from "./pages/Events";
import { Explorer } from "./pages/Explorer";
import { Flows } from "./pages/Flows";
import { Integrations } from "./pages/Integrations";
import { Login } from "./pages/Login";
import { ServiceDetail } from "./pages/ServiceDetail";
import { Services } from "./pages/Services";
import { SettingsPage } from "./pages/Settings";
import { Sync } from "./pages/Sync";
import { WorkspaceOverview } from "./pages/WorkspaceOverview";
import { Workspaces } from "./pages/Workspaces";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 5000 } },
});

function Protected({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="grid min-h-screen place-items-center"><Spinner label="Authenticating…" /></div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <ToastProvider>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/" element={<Protected><Workspaces /></Protected>} />
              <Route
                path="/w/:workspaceId"
                element={<Protected><Layout><Outlet /></Layout></Protected>}
              >
                <Route index element={<WorkspaceOverview />} />
                <Route path="services" element={<Services />} />
                <Route path="services/:serviceId" element={<ServiceDetail />} />
                <Route path="explorer" element={<Explorer />} />
                <Route path="flows" element={<Flows />} />
                <Route path="events" element={<Events />} />
                <Route path="docs" element={<Docs />} />
                <Route path="ai" element={<AI />} />
                <Route path="integrations" element={<Integrations />} />
                <Route path="sync" element={<Sync />} />
                <Route path="settings" element={<SettingsPage />} />
              </Route>
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
