export type Status =
  | "running"
  | "stopped"
  | "error"
  | "open"
  | "closed"
  | "connected"
  | "unknown"
  | "not_configured";

export default function StatusBadge({
  status,
  label: labelOverride,
}: {
  status: Status;
  label?: string;
}) {
  const map: Record<string, { label: string; className: string }> = {
    running: { label: "Running", className: "bg-gain/15 text-gain" },
    open: { label: "Open", className: "bg-gain/15 text-gain" },
    connected: { label: "Connected", className: "bg-gain/15 text-gain" },
    stopped: { label: "Stopped", className: "bg-loss/15 text-loss" },
    closed: { label: "Closed", className: "bg-loss/15 text-loss" },
    error: { label: "Error", className: "bg-loss/15 text-loss" },
    unknown: { label: "Unknown", className: "bg-bg-panel2 text-muted" },
    not_configured: { label: "Not configured", className: "bg-bg-panel2 text-muted" },
  };
  const { label, className } = map[status] ?? map.stopped;

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${className}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {labelOverride ?? label}
    </span>
  );
}
