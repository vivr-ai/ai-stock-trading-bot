"use client";

/**
 * Horizontal tab bar for the pages within the CURRENT section, derived
 * entirely from dashboard/lib/navigation.ts. Renders nothing on Home (Home
 * has no "pages" of its own) and nothing if the current path doesn't match
 * any configured page (e.g. /login).
 *
 * Sections with a flat `pages` list get a single tab row. Sections with
 * `groups` (currently only Intelligence) get two tiers: a group selector
 * row, then that group's pages as a second tab row - this is the
 * "tab-groups" mechanism from the redesign proposal that lets a section
 * absorb more pages later without a new nav paradigm.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  findSectionForPath,
  findGroupForPath,
  type NavGroup,
  type NavPage,
} from "@/lib/navigation";

function TabRow({ pages, pathname }: { pages: NavPage[]; pathname: string }) {
  return (
    <div className="flex gap-1 overflow-x-auto">
      {pages.map((page) => {
        const active = pathname === page.href || pathname.startsWith(page.href + "/");
        return (
          <Link
            key={page.href}
            href={page.href}
            className={`flex shrink-0 items-center gap-2 whitespace-nowrap rounded-t-lg border-b-2 px-3 py-2 text-sm transition-colors ${
              active
                ? "border-accent text-accent font-medium"
                : "border-transparent text-muted hover:text-white"
            }`}
          >
            <page.icon size={15} strokeWidth={2} />
            {page.label}
          </Link>
        );
      })}
    </div>
  );
}

function GroupRow({ groups, activeGroup }: { groups: NavGroup[]; activeGroup: NavGroup }) {
  return (
    <div className="flex gap-1 overflow-x-auto pt-2">
      {groups.map((group) => {
        const active = group.id === activeGroup.id;
        return (
          <Link
            key={group.id}
            href={group.pages[0]?.href ?? "#"}
            className={`shrink-0 whitespace-nowrap rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              active
                ? "bg-accent/15 text-accent"
                : "bg-bg-panel2 text-muted hover:text-white"
            }`}
          >
            {group.label}
          </Link>
        );
      })}
    </div>
  );
}

export default function SectionTabs() {
  const pathname = usePathname();
  if (pathname === "/login" || pathname === "/") return null;

  const section = findSectionForPath(pathname);
  if (!section) return null;

  if (section.groups) {
    const activeGroup = findGroupForPath(section, pathname) ?? section.groups[0];
    return (
      <div className="mb-6 border-b border-bg-border">
        <GroupRow groups={section.groups} activeGroup={activeGroup} />
        <TabRow pages={activeGroup.pages} pathname={pathname} />
      </div>
    );
  }

  if (section.pages) {
    return (
      <div className="mb-6 border-b border-bg-border">
        <TabRow pages={section.pages} pathname={pathname} />
      </div>
    );
  }

  return null;
}
