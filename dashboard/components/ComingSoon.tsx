export default function ComingSoon({ title }: { title: string }) {
  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold text-white">{title}</h1>
      <p className="mb-6 text-sm text-muted">This page is next up, after Home is approved.</p>
      <div className="rounded-xl border border-dashed border-bg-border bg-bg-panel p-10 text-center text-sm text-muted">
        Built in the next phase.
      </div>
    </div>
  );
}
