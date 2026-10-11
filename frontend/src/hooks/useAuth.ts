import { createContext, useContext } from "react";
import type { KaareUser } from "../services/api";

export type AuthContextValue = {
  user: KaareUser | null;
  loading: boolean;
  login: (user: KaareUser, token: string) => void;
  logout: () => void;
};

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
