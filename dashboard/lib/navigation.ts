/**
 * Single source of truth for the dashboard's navigation: sidebar sections,
 * horizontal section-tabs, optional tab-groups, the mobile bottom bar +
 * sheet, and the command palette all read from NAV_SECTIONS below.
 *
 * See docs/dashboard-ux-redesign-proposal.md (approved Phase 1 proposal)
 * for the full reasoning. Summary of the model:
 *
 *   Section (sidebar, ~5 fixed items, never grows)
 *     └─ Group (optional — only appears once a section exceeds ~7 pages)
 *          └─ Page (tab / list item)
 *
 * Adding a page anywhere in the app should be a one-line entry in this
 * file, never a new component and never a new sidebar item. Every href
 * here is an EXISTING route - this file is a navigation/labelling layer
 * on top of pages that already exist; it renames how a few are displayed
 * (see NAME NOTE below) but changes no URLs and no page content.
 *
 * NAME NOTE: three renames adopted from the redesign brief, plus two more
 * (see docs/dashboard-ux-redesign-proposal.md §2.3):
 *   AI Decision Log      -> AI Decisions       (route unchanged: /decisions)
 *   Trading Strategy     -> Current Strategy   (route unchanged: /strategy)
 *   Strategy Intelligence-> Learning & Insights (route unchanged: /strategy-intelligence)
 *   Risk Dashboard        -> Risk               (route unchanged: /risk)
 * These are DISPLAY LABEL renames only - bookmarks, links, and the
 * underlying page files (dashboard/app/strategy/page.tsx etc.) are
 * untouched.
 */
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
  type LucideIcon,
} from "lucide-react";

export type NavPage = {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Short description shown in the command palette's secondary line. */
  description?: string;
};

export type NavGroup = {
  id: string;
  label: string;
  pages: NavPage[];
};

export type NavSection = {
  id: string;
  label: string;
  icon: LucideIcon;
  /** Section's own landing page - the first page a user lands on when
   * clicking the section in the sidebar. */
  href: string;
  /** Sections with <=~7 pages use a flat page list (single-tier tabs). */
  pages?: NavPage[];
  /** Sections that have outgrown a flat tab bar use named groups instead -
   * this becomes a two-tier tab bar (group selector, then pages within the
   * active group). A section has either `pages` or `groups`, never both. */
  groups?: NavGroup[];
};

/** Pinned above all sections, always first, not part of NAV_SECTIONS since
 * Home is a standalone cockpit view rather than a "section" of pages. */
export const HOME_PAGE: NavPage = {
  href: "/",
  label: "Home",
  icon: LayoutDashboard,
  description: "Status-at-a-glance cockpit",
};

export const NAV_SECTIONS: NavSection[] = [
  {
    id: "trading",
    label: "Trading",
    icon: Briefcase,
    href: "/portfolio",
    pages: [
      { href: "/portfolio", label: "Portfolio", icon: Briefcase, description: "Current holdings and cash" },
      { href: "/trades", label: "Trade History", icon: History, description: "Every buy/sell/auto-exit" },
      { href: "/performance", label: "Performance", icon: LineChart, description: "Win rate, expectancy, equity curve" },
    ],
  },
  {
    id: "intelligence",
    label: "Intelligence",
    icon: BrainCircuit,
    href: "/decisions",
    // Heaviest section (7 pages today) - first to use the group tier.
    groups: [
      {
        id: "decisions",
        label: "Decisions",
        pages: [
          { href: "/strategy", label: "Current Strategy", icon: BookOpen, description: "The rules currently trading" },
          { href: "/decisions", label: "AI Decisions", icon: Bot, description: "Every scan/buy/sell/skip this cycle" },
        ],
      },
      {
        id: "research",
        label: "Research",
        pages: [
          { href: "/strategy-intelligence", label: "Learning & Insights", icon: BrainCircuit, description: "Performance analytics + AI research assistant" },
          { href: "/pattern-discovery", label: "Pattern Discovery", icon: Search, description: "Statistical findings from trade history" },
        ],
      },
      {
        id: "governance",
        label: "Governance",
        pages: [
          { href: "/strategy-versions", label: "Strategy Versions", icon: GitBranch, description: "Deployed rule sets + performance by version" },
          { href: "/strategy-health", label: "Strategy Health", icon: Gauge, description: "Composite health score" },
          { href: "/recommendations", label: "Recommendations", icon: ClipboardCheck, description: "AI-drafted changes awaiting your review" },
        ],
      },
      // "Advanced" group (AI Memory, Prompt Management, Reinforcement
      // Learning) is reserved for when those pages actually exist - see
      // docs/dashboard-ux-redesign-proposal.md §2.2. Intentionally omitted
      // here rather than shipped as an empty tab.
    ],
  },
  {
    id: "risk",
    label: "Risk & Safety",
    icon: ShieldAlert,
    href: "/risk",
    pages: [
      { href: "/risk", label: "Risk", icon: ShieldAlert, description: "Exposure, drawdown, kill-switch state" },
      { href: "/live-readiness", label: "Live Readiness", icon: ShieldCheck, description: "Pre-flight checks before real money trades" },
    ],
  },
  {
    id: "reports",
    label: "Reports",
    icon: FileSpreadsheet,
    href: "/monthly-report",
    pages: [
      { href: "/monthly-report", label: "Monthly Report", icon: FileClock, description: "AI-narrated monthly rollup" },
      { href: "/accountant-export", label: "Accountant Export", icon: FileSpreadsheet, description: "AU tax / US broker exports" },
    ],
  },
  {
    id: "system",
    label: "System",
    icon: HeartPulse,
    href: "/system-health",
    pages: [
      { href: "/system-health", label: "System Health", icon: HeartPulse, description: "Heartbeats, scheduler, broker connectivity" },
      { href: "/notifications", label: "Notifications", icon: Bell, description: "Notification feed" },
      { href: "/notification-settings", label: "Notification Settings", icon: Settings, description: "Telegram / channel routing per event type" },
    ],
  },
];

// ---- Derived helpers - every consumer (Sidebar, SectionTabs, mobile nav,
// command palette) should read through these rather than re-walking the
// tree itself, so there's exactly one place that understands the shape. ----

/** Flat list of every page in a section, regardless of whether it uses
 * `pages` or `groups`. */
export function pagesForSection(section: NavSection): NavPage[] {
  if (section.pages) return section.pages;
  if (section.groups) return section.groups.flatMap((g) => g.pages);
  return [];
}

/** Finds which section a given pathname belongs to (longest-href-match,
 * so e.g. "/strategy-versions" doesn't accidentally match "/strategy"). */
export function findSectionForPath(pathname: string): NavSection | undefined {
  let best: { section: NavSection; len: number } | undefined;
  for (const section of NAV_SECTIONS) {
    for (const page of pagesForSection(section)) {
      if (pathname === page.href || pathname.startsWith(page.href + "/")) {
        if (!best || page.href.length > best.len) {
          best = { section, len: page.href.length };
        }
      }
    }
  }
  return best?.section;
}

/** Finds which group within a section a pathname belongs to (undefined if
 * the section has no groups, or the path is the section's own page list). */
export function findGroupForPath(section: NavSection, pathname: string): NavGroup | undefined {
  if (!section.groups) return undefined;
  return section.groups.find((g) =>
    g.pages.some((p) => pathname === p.href || pathname.startsWith(p.href + "/"))
  );
}

export type FlatNavEntry = {
  page: NavPage;
  sectionLabel: string;
  groupLabel?: string;
};

/** Every page in the whole app, flattened with breadcrumb-style labels -
 * this is exactly what the command palette (Cmd+K) fuzzy-searches over,
 * and what a future page-count audit would iterate. */
export function getAllNavPages(): FlatNavEntry[] {
  const entries: FlatNavEntry[] = [
    { page: HOME_PAGE, sectionLabel: "Home" },
  ];
  for (const section of NAV_SECTIONS) {
    if (section.pages) {
      for (const page of section.pages) {
        entries.push({ page, sectionLabel: section.label });
      }
    }
    if (section.groups) {
      for (const group of section.groups) {
        for (const page of group.pages) {
          entries.push({ page, sectionLabel: section.label, groupLabel: group.label });
        }
      }
    }
  }
  return entries;
}
