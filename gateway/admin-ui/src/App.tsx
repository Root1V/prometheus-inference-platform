import type { ReactNode } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { SessionExpiryBanner } from "./components/SessionExpiryBanner";
import { ToastProvider } from "./context/ToastContext";
import Billing from "./routes/Billing";
import Dashboard from "./routes/Dashboard";
import Limits from "./routes/Limits";
import Login from "./routes/Login";
import Models from "./routes/Models";
import Nodes from "./routes/Nodes";
import Overview from "./routes/Overview";
import Playground from "./routes/Playground";
import Activity from "./routes/Activity";
import Usage from "./routes/Usage";
import Users from "./routes/Users";

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <SessionExpiryBanner />
        <HashRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <Overview />
                </ProtectedRoute>
              }
            />
            <Route
              path="/instances"
              element={
                <ProtectedRoute>
                  <Dashboard />
                </ProtectedRoute>
              }
            />
            <Route
              path="/models"
              element={
                <ProtectedRoute>
                  <Models />
                </ProtectedRoute>
              }
            />
            <Route
              path="/usage"
              element={
                <ProtectedRoute>
                  <Usage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/users"
              element={
                <ProtectedRoute>
                  <Users />
                </ProtectedRoute>
              }
            />
            <Route
              path="/billing"
              element={
                <ProtectedRoute>
                  <Billing />
                </ProtectedRoute>
              }
            />
            <Route
              path="/nodes"
              element={
                <ProtectedRoute>
                  <Nodes />
                </ProtectedRoute>
              }
            />
            <Route
              path="/limits"
              element={
                <ProtectedRoute>
                  <Limits />
                </ProtectedRoute>
              }
            />
            <Route
              path="/activity"
              element={
                <ProtectedRoute>
                  <Activity />
                </ProtectedRoute>
              }
            />
            {/* PRM-235: the old path still resolves. A bookmarked /#/sessions
                landing on a 404 would be the page telling an operator it was
                deleted, when it was renamed. */}
            <Route
              path="/sessions"
              element={<Navigate to="/activity" replace />}
            />
            <Route
              path="/playground"
              element={
                <ProtectedRoute>
                  <Playground />
                </ProtectedRoute>
              }
            />
          </Routes>
        </HashRouter>
      </ToastProvider>
    </AuthProvider>
  );
}
