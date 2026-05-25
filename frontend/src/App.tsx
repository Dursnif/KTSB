import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { useTranslation } from "react-i18next";
import { apiGetLanguage } from "./services/api";

import Login from "./pages/Login";
import AdminLayout from "./layouts/AdminLayout";
import UserLayout from "./layouts/UserLayout";
import Dashboard from "./pages/admin/Dashboard";
import Users from "./pages/admin/Users";
import System from "./pages/admin/System";
import AgentMessages from "./pages/admin/AgentMessages";
import Reflections from "./pages/admin/Reflections";
import Tools from "./pages/admin/Tools";
import Settings from "./pages/admin/Settings";
import Aliases from "./pages/admin/Aliases";
import Nodes from "./pages/admin/Nodes";
import Onboarding from "./pages/Onboarding";
import Home from "./pages/user/Home";
import UserReflections from "./pages/user/UserReflections";
import UserSettings from "./pages/user/UserSettings";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  return user ? <>{children}</> : <Navigate to="/login" replace />;
}

function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}

function LanguageSync() {
  const { i18n } = useTranslation();
  useEffect(() => {
    apiGetLanguage().then(r => {
      const lang = r.language || "nb";
      localStorage.setItem("kaare_lang", lang);
      i18n.changeLanguage(lang);
    }).catch(() => {});
  }, [i18n]);
  return null;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />

      {/* ADMIN */}
      <Route path="/admin" element={<RequireAdmin><AdminLayout /></RequireAdmin>}>
        <Route index element={<Dashboard />} />
        <Route path="users" element={<Users />} />
        <Route path="system" element={<System />} />
        <Route path="agent-messages" element={<AgentMessages />} />
        <Route path="reflections" element={<Reflections />} />
        <Route path="tools" element={<Tools />} />
        <Route path="aliases" element={<Aliases />} />
        <Route path="nodes" element={<Nodes />} />
        <Route path="settings" element={<Settings />} />
      </Route>

      {/* ONBOARDING */}
      <Route path="/onboarding" element={<RequireAdmin><Onboarding /></RequireAdmin>} />

      {/* USER */}
      <Route path="/" element={<RequireAuth><UserLayout /></RequireAuth>}>
        <Route index element={<Home />} />
        <Route path="reflections" element={<UserReflections />} />
        <Route path="settings" element={<UserSettings />} />
      </Route>

      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <LanguageSync />
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
