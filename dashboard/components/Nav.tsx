"use client";

/**
 * SUPERSEDED - no longer imported anywhere (see Shell.tsx, which now
 * composes Sidebar.tsx + SectionTabs.tsx + MobileSectionBar.tsx instead).
 * Kept in the repo only because this environment can't delete files handed
 * to it here; safe to delete this file the next time you're editing
 * locally - nothing references it. See docs/dashboard-ux-redesign-
 * proposal.md for why the flat link list below was replaced.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";
import {
  LayoutDashboard,
  Briefcase,
  History,
  LineChart,
  Bot,
  BookOpen,
  ShieldAlert,
  Bell,
  Settings,
  HeartPulse,
  FileSpreadsheet,
  ShieldCheck,
  BrainCircuit,
  GitBranch,
  ClipboardCheck,
  Search,
  Gauge,
  FileClock,
  LogOut,
  Menu,
  X,
} from "lucide-react";
import { useState } from "react";

const LINKS = [
  { href: "/", label: "Home", icon: LayoutDashboard },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
  { href: "/trades", label: "Trade History", icon: History },
  { href: "/performance", label: "Performance", icon: LineChart },
  { href: "/decisions", label: "AI Decision Log", icon: Bot },
  { href: "/strategy", label: "Trading Strategy", icon: BookOpen },
  { href: "/strategy-intelligence", label: "Strategy Intelligence", icon: BrainCircuit },
  { href: "/strategy-versions", label: "Strategy Versions", icon: GitBranch },
  { href: "/pattern-discovery", label: "Pattern Discovery", icon: Search },
  { href: "/strategy-health", label: "Strategy Health", icon: Gauge },
  { href: "/recommendations", label: "Recommendations", icon: ClipboardCheck },
  { href: "/monthly-report", label: "Monthly Report", icon: FileClock },
  { href: "/risk", label: "Risk Dashboard", icon: ShieldAlert },
  { href: "/system-health", label: "System Health", icon: HeartPulse },
  { href: "/live-readiness", label: "Live Readiness", icon: ShieldCheck },
  { href: "/notifications", label: "Notifications", icon: Bell },
  { href: "/notification-settings", label: "Notification Settings", icon: Settings },
  { href: "/accountant-export", label: "Accountant Export", icon: FileSpreadsheet },
];

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="flex flex-col gap-1">
      {LINKS.map(({ href, label, icon: Icon }) => {
        const active = pathname === href;
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
              active
                ? "bg-accent/15 text-accent font-medium"
                : "text-muted hover:bg-bg-panel2 hover:text-white"
            }`}
          >
            <Icon size={17} strokeWidth={2} />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

export default function Nav() {
  const [open, setOpen] = useState(false);

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden md:flex md:w-60 md:flex-col md:border-r md:border-bg-border md:bg-bg-panel md:px-4 md:py-6 md:h-screen md:sticky md:top-0">
        <div className="mb-6 px-2">
          <div className="text-sm font-semibold tracking-wide text-white">Trading Bot</div>
          <div className="text-xs text-muted">Monitoring dashboard</div>
        </div>
        <div className="flex-1 overflow-y-auto">
          <NavLinks />
        </div>
        <button
          onClick={() => signOut({ callbackUrl: "/login" })}
          className="mt-4 flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted hover:bg-bg-panel2 hover:text-white"
        >
          <LogOut size={17} />
          Sign out
        </button>
      </aside>

      {/* Mobile top bar */}
      <div className="flex items-center justify-between border-b border-bg-border bg-bg-panel px-4 py-3 md:hidden">
        <div className="text-sm font-semibold text-white">Trading Bot</div>
        <button onClick={() => setOpen(true)} aria-label="Open menu">
          <Menu size={22} />
        </button>
      </div>

      {/* Mobile drawer */}
      {open && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-0 h-full w-72 bg-bg-panel p-4 shadow-xl">
            <div className="mb-4 flex items-center justify-between">
              <div className="text-sm font-semibold text-white">Trading Bot</div>
              <button onClick={() => setOpen(false)} aria-label="Close menu">
                <X size={22} />
              </button>
            </div>
            <NavLinks onNavigate={() => setOpen(false)} />
            <button
              onClick={() => signOut({ callbackUrl: "/login" })}
              className="mt-4 flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted hover:bg-bg-panel2 hover:text-white"
            >
              <LogOut size={17} />
              Sign out
            </button>
          </div>
        </div>
      )}
    </>
  );
}
