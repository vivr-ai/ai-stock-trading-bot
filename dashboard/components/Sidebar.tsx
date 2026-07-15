"use client";

/**
 * Section-only sidebar - replaces the old flat Nav.tsx link list. Renders
 * Home (pinned) + NAV_SECTIONS from dashboard/lib/navigation.ts and nothing
 * else, by design: this list is meant to stay at ~6 items permanently (see
 * docs/dashboard-ux-redesign-proposal.md §2.1). Individual pages within a
 * section live in SectionTabs.tsx, not here.
 *
 * Clicking a section navigates to that section's own landing page
 * (`section.href`) - SectionTabs then shows that section's pages as tabs.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut } from "next-auth/react";
import { LogOut, Menu, X, Search as SearchIcon } from "lucide-react";
import { useState } from "react";
import { HOME_PAGE, NAV_SECTIONS, findSectionForPath, type NavSection } from "@/lib/navigation";

function RailLinks({
  onNavigate,
  onOpenPalette,
}: {
  onNavigate?: () => void;
  onOpenPalette?: () => void;
}) {
  const pathname = usePathname();
  const activeSection = findSectionForPath(pathname);
  const isHome = pathname === "/";

  const itemClass = (active: boolean) =>
    `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
      active
        ? "bg-accent/15 text-accent font-medium"
        : "text-muted hover:bg-bg-panel2 hover:text-white"
    }`;

  return (
    <nav className="flex flex-col gap-1">
      <Link href={HOME_PAGE.href} onClick={onNavigate} className={itemClass(isHome)}>
        <HOME_PAGE.icon size={17} strokeWidth={2} />
        {HOME_PAGE.label}
      </Link>

      <div className="my-2 border-t border-bg-border" />

      {NAV_SECTIONS.map((section: NavSection) => {
        const active = activeSection?.id === section.id;
        return (
          <Link
            key={section.id}
            href={section.href}
            onClick={onNavigate}
            className={itemClass(active)}
          >
            <section.icon size={17} strokeWidth={2} />
            {section.label}
          </Link>
        );
      })}

      {onOpenPalette && (
        <>
          <div className="my-2 border-t border-bg-border" />
          <button
            onClick={onOpenPalette}
            className="flex items-center justify-between gap-3 rounded-lg px-3 py-2 text-sm text-muted hover:bg-bg-panel2 hover:text-white"
          >
            <span className="flex items-center gap-3">
              <SearchIcon size={17} strokeWidth={2} />
              Search
            </span>
            <kbd className="rounded border border-bg-border bg-bg-panel2 px-1.5 py-0.5 text-[10px] text-muted">
              ⌘K
            </kbd>
          </button>
        </>
      )}
    </nav>
  );
}

export default function Sidebar({ onOpenPalette }: { onOpenPalette?: () => void }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden md:flex md:w-56 md:flex-col md:border-r md:border-bg-border md:bg-bg-panel md:px-4 md:py-6 md:h-screen md:sticky md:top-0">
        <div className="mb-6 px-2">
          <div className="text-sm font-semibold tracking-wide text-white">Trading Bot</div>
          <div className="text-xs text-muted">Monitoring dashboard</div>
        </div>
        <div className="flex-1 overflow-y-auto">
          <RailLinks onOpenPalette={onOpenPalette} />
        </div>
        <button
          onClick={() => signOut({ callbackUrl: "/login" })}
          className="mt-4 flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted hover:bg-bg-panel2 hover:text-white"
        >
          <LogOut size={17} />
          Sign out
        </button>
      </aside>

      {/* Mobile top bar - the bottom section bar (Task #24) is the primary
          mobile nav; this top bar remains as a fallback entry point that
          also exposes sign-out and search, which don't fit a bottom bar. */}
      <div className="flex items-center justify-between border-b border-bg-border bg-bg-panel px-4 py-3 md:hidden">
        <div className="text-sm font-semibold text-white">Trading Bot</div>
        <div className="flex items-center gap-3">
          {onOpenPalette && (
            <button onClick={onOpenPalette} aria-label="Search">
              <SearchIcon size={20} />
            </button>
          )}
          <button onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu size={22} />
          </button>
        </div>
      </div>

      {/* Mobile drawer (full section+page list, for anything not reachable
          from the bottom bar - e.g. sign out) */}
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
            <RailLinks onNavigate={() => setOpen(false)} onOpenPalette={onOpenPalette} />
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
