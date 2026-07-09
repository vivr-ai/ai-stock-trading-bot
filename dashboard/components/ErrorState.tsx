import { AlertTriangle } from "lucide-react";

export default function ErrorState({
  message = "Something went wrong loading this data.",
}: {
  message?: string;
}) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-loss/30 bg-loss/10 p-4 text-sm text-loss">
      <AlertTriangle size={18} className="mt-0.5 shrink-0" />
      <div>
        <div className="font-medium">Couldn&apos;t load this</div>
        <div className="mt-0.5 text-loss/80">{message}</div>
      </div>
    </div>
  );
}
