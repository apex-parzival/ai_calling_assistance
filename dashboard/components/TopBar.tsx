"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useStaff } from "@/lib/auth";

export function TopBar() {
  const staff = useStaff();
  const router = useRouter();
  return (
    <header className="topbar">
      <Link href="/" className="brand">Front Desk</Link>
      <span className="spacer" />
      <span className="who">{staff.name}</span>
      <button className="btn small" onClick={async () => { await api.logout().catch(() => {}); router.replace("/login"); }}>
        Sign out
      </button>
    </header>
  );
}
