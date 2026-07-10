"use client";

import type { ReactNode } from "react";

export default function Term({
  definition,
  children,
}: {
  definition: string;
  children: ReactNode;
}) {
  return (
    <span className="group relative inline-block cursor-help border-b border-dotted border-muted">
      {children}
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-2 hidden w-64 -translate-x-1/2 rounded-lg border border-bg-border bg-bg-panel2 p-3 text-xs font-normal leading-relaxed text-white shadow-xl group-hover:block group-focus:block"
      >
        {definition}
      </span>
    </span>
  );
}
