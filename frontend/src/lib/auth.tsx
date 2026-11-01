import React, { createContext, useContext, useEffect, useMemo, useState } from "react";
import { get, post, setToken, getToken } from "./api";

export type User = { id: string; email: string; name: string; role: string };
export type WorkspaceRef = { id: string; name: string; slug: string };

type AuthState = {
  user: User | null;
  workspaces: WorkspaceRef[];
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  bootstrap: (email: string, name: string, password: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
};

const AuthCtx = createContext<AuthState>({} as AuthState);
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [workspaces, setWorkspaces] = useState<WorkspaceRef[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await get<{ user: User; workspaces: WorkspaceRef[] }>("/auth/me");
      setUser(me.user);
      setWorkspaces(me.workspaces);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, []);

  const login = async (email: string, password: string) => {
    const out = await post<{ access_token: string; user: User }>("/auth/login", { email, password });
    setToken(out.access_token);
    await refresh();
  };

  const bootstrap = async (email: string, name: string, password: string) => {
    const out = await post<{ access_token: string; user: User }>("/auth/bootstrap", { email, name, password });
    setToken(out.access_token);
    await refresh();
  };

  const logout = () => {
    setToken(null);
    setUser(null);
    setWorkspaces([]);
  };

  const value = useMemo(
    () => ({ user, workspaces, loading, login, bootstrap, logout, refresh }),
    [user, workspaces, loading],
  );
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}
