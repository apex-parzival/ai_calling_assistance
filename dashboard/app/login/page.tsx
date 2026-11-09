"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(email, password);
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Wrong email or password." : "Can't reach the clinic server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <form className="card login" onSubmit={submit}>
        <div className="pad">
          <h1>Front desk sign in</h1>
          <p>Calls handled by the AI receptionist.</p>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" autoComplete="username" required value={email}
                   onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input id="password" type="password" autoComplete="current-password" required value={password}
                   onChange={(e) => setPassword(e.target.value)} />
          </div>
          <button className="btn primary" type="submit" disabled={busy} style={{ width: "100%", justifyContent: "center" }}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </form>
    </main>
  );
}
