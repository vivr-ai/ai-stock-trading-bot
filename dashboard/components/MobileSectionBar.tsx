"use client";

/**
 * Bottom icon bar for sections, mobile only (hidden md+, where the desktop
 * Sidebar takes over) - thumb-reachable section switching, the mobile
 * convention used by Linear/Vercel's mobile web (see docs/dashboard-ux-
 * redesign-proposal.md §4). Deliberately just Home + the 5 sections (never
 * grows) - picking a specific PAGE within a section happens via the
 * horizontal tabs SectionTabs.tsx already renders at the top of the content
 * area on every screen size, so this bar doesn't need its own slide-up
 * sheet or duplicate that page list.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search as SearchIcon } from "lucide-react";
import { HOME_PAGE, NAV_SECTIONS, findSectionForPath } from "@/lib/navigation";

export default function MobileSectionBar({ onOpenPalette }: { onOpenPalette?: () => void }) {
  const pathname = usePathname();
  if (pathname === "/login") return null;

  const activeSection = findSectionForPath(pathname);
  const isHome = pathname === "/";

  const itemClass = (active: boolean) =>
    `flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px] ${
      active ? "text-accent" : "text-muted"
    }`;

  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 flex border-t border-bg-border bg-bg-panel md:hidden">
      <Link href={HOME_PAGE.href} className={itemClass(isHome)}>
        <HOME_PAGE.icon size={19} strokeWidth={2} />
        {HOME_PAGE.label}
      </Link>
      {NAV_SECTIONS.map((section) => {
        const active = activeSection?.id === section.id;
        return (
          <Link key={section.id} href={section.href} className={itemClass(active)}>
            <section.icon size={19} strokeWidth={2} />
            {section.label}
          </Link>
        );
      })}
      {onOpenPalette && (
        <button onClick={onOpenPalette} className={itemClass(false)}>
          <SearchIcon size={19} strokeWidth={2} />
          Search
        </button>
      )}
    </nav>
  );
}
