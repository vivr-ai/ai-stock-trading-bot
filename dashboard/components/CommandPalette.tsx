"use client";

/**
 * Cmd+K / Ctrl+K command palette - fuzzy search over every page in the app,
 * reading the same NAV_SECTIONS config that drives the Sidebar and
 * SectionTabs (see dashboard/lib/navigation.ts). This is the redesign
 * proposal's actual long-horizon answer to "50 pages without a future
 * redesign" (§2.4): hierarchy (sidebar -> tabs -> groups) solves
 * orientation, this solves fast access at scale. Deliberately minimal per
 * the approved proposal - a list + substring/subsequence filter, no AI
 * features.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import { getAllNavPages, type FlatNavEntry } from "@/lib/navigation";

const ALL_PAGES = getAllNavPages();

/** Cheap fuzzy-ish scorer: exact label match first, then label starts-with,
 * then substring anywhere in label/description/section/group, then a
 * subsequence match (letters of the query appear in order, not necessarily
 * contiguous) as a last resort. Lower score = better match. Returns null if
 * the query doesn't match at all. */
function score(entry: FlatNavEntry, query: string): number | null {
  const q = query.trim().toLowerCase();
  if (!q) return 0;
  const label = entry.page.label.toLowerCase();
  const haystack = [
    label,
    entry.page.description?.toLowerCase() ?? "",
    entry.sectionLabel.toLowerCase(),
    entry.groupLabel?.toLowerCase() ?? "",
  ].join(" ");

  if (label === q) return 0;
  if (label.startsWith(q)) return 1;
  if (haystack.includes(q)) return 2 + haystack.indexOf(q) / 1000;

  // Subsequence fallback (e.g. "stgh" -> "Strategy Health").
  let qi = 0;
  for (let i = 0; i < label.length && qi < q.length; i++) {
    if (label[i] === q[qi]) qi++;
  }
  if (qi === q.length) return 3;

  return null;
}

export default function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  const results = useMemo(() => {
    if (!open) return [];
    return ALL_PAGES
      .map((entry) => ({ entry, s: score(entry, query) }))
      .filter((r): r is { entry: FlatNavEntry; s: number } => r.s !== null)
      .sort((a, b) => a.s - b.s)
      .slice(0, 20)
      .map((r) => r.entry);
  }, [query, open]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
      // Focus after the overlay mounts.
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  const navigateTo = (href: string) => {
    router.push(href);
    onClose();
  };

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (!open) return;
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => Math.min(i + 1, results.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const chosen = results[activeIndex];
        if (chosen) navigateTo(chosen.page.href);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, results, activeIndex]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center pt-24">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative w-full max-w-lg overflow-hidden rounded-xl border border-bg-border bg-bg-panel shadow-2xl">
        <div className="flex items-center gap-3 border-b border-bg-border px-4 py-3">
          <Search size={18} className="text-muted" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Jump to a page..."
            className="flex-1 bg-transparent text-sm text-white placeholder:text-muted focus:outline-none"
          />
          <kbd className="rounded border border-bg-border bg-bg-panel2 px-1.5 py-0.5 text-[10px] text-muted">
            Esc
          </kbd>
        </div>
        <div className="max-h-80 overflow-y-auto py-2">
          {results.length === 0 && (
            <div className="px-4 py-6 text-center text-sm text-muted">No matching pages.</div>
          )}
          {results.map((entry, i) => (
            <button
              key={entry.page.href}
              onClick={() => navigateTo(entry.page.href)}
              onMouseEnter={() => setActiveIndex(i)}
              className={`flex w-full items-center gap-3 px-4 py-2 text-left text-sm transition-colors ${
                i === activeIndex ? "bg-accent/15 text-accent" : "text-white hover:bg-bg-panel2"
              }`}
            >
              <entry.page.icon size={16} strokeWidth={2} />
              <span className="flex-1">
                <span className="block font-medium">{entry.page.label}</span>
                <span className="block text-xs text-muted">
                  {entry.groupLabel
                    ? `${entry.sectionLabel} · ${entry.groupLabel}`
                    : entry.sectionLabel}
                </span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
