export default function StatusBadge({
  status,
}: {
  status: "running" | "stopped" | "error" | "open" | "closed";
}) {
  const map: Record<string, { label: string; className: string }> = {
    running: { label: "Running", className: "bg-gain/15 text-gain" },
    open: { label: "Open", className: "bg-gain/15 text-gain" },
    stopped: { label: "Stopped", className: "bg-loss/15 text-loss" },
    closed: { label: "Closed", className: "bg-loss/15 text-loss" },
    error: { label: "Error", className: "bg-loss/15 text-loss" },
  };
  const { label, className } = map[status] ?? map.stopped;

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${className}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {label}
    </span>
  );
}
