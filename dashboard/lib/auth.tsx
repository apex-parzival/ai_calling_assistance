"use client";

import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, type Staff } from "./api";

const AuthContext = createContext<Staff | null>(null);

export function useStaff(): Staff {
  const s = useContext(AuthContext);
  if (!s) throw new Error("useStaff outside RequireAuth");
  return s;
}

/** Renders children only for a signed-in staff member; otherwise sends them to /login. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [staff, setStaff] = useState<Staff | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const check = () =>
      api
        .me()
        .then((s) => {
          setError(null);
          setStaff(s);
        })
        .catch((e) => {
          if (e instanceof ApiError && e.status === 401) {
            router.replace("/login");
            return;
          }
          // Backend down or restarting: keep retrying instead of leaving the desk on an error page.
          setError("Can't reach the clinic server. Retrying…");
          timer = setTimeout(check, 3000);
        });
    check();
    return () => clearTimeout(timer);
  }, [router]);

  if (error) return <p className="page-message error">{error}</p>;
  if (!staff) return <p className="page-message">Loading…</p>;
  return <AuthContext.Provider value={staff}>{children}</AuthContext.Provider>;
}
