"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import Sidebar from "./Sidebar";
import SectionTabs from "./SectionTabs";
import CommandPalette from "./CommandPalette";
import MobileSectionBar from "./MobileSectionBar";

export default function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === "/login";
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Global ⌘K / Ctrl+K shortcut, available from anywhere in the app (not
  // just when a Sidebar button has focus) - this is what makes the palette
  // the actual fast-access path at scale (see docs/dashboard-ux-redesign-
  // proposal.md §2.4), not just a discoverable-but-rarely-used button.
  useEffect(() => {
    if (isLogin) return;
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isLogin]);

  if (isLogin) {
    return <div className="min-h-screen bg-bg">{children}</div>;
  }

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <Sidebar onOpenPalette={() => setPaletteOpen(true)} />
      <main className="flex-1 px-4 py-6 pb-20 md:px-8 md:py-8 md:pb-8">
        <div className="mx-auto max-w-6xl">
          <SectionTabs />
          {children}
        </div>
      </main>
      <MobileSectionBar onOpenPalette={() => setPaletteOpen(true)} />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
