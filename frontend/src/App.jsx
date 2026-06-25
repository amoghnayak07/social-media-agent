// Phase 0 placeholder. Confirms Tailwind v4 + the design tokens render.
// The real surfaces (approval queue, bucket view, policy editor) come in later phases.

const categories = [
  { label: 'simple_positive', cls: 'bg-cat-positive' },
  { label: 'question', cls: 'bg-cat-question' },
  { label: 'brand_inquiry', cls: 'bg-cat-brand' },
  { label: 'criticism', cls: 'bg-cat-criticism' },
  { label: 'sensitive', cls: 'bg-cat-sensitive' },
  { label: 'spam', cls: 'bg-cat-spam' },
  { label: 'other', cls: 'bg-cat-other' },
]

function App() {
  return (
    <div className="min-h-screen bg-bg text-text">
      <header className="border-b border-border px-6 py-4">
        <h1 className="font-display text-lg font-semibold tracking-tight">
          Comment Agent
        </h1>
      </header>

      <main className="mx-auto max-w-2xl px-6 py-16">
        <div className="rounded-panel border border-border bg-surface p-8">
          <p className="font-mono text-xs uppercase tracking-widest text-text-muted">
            Phase 0 · skeleton
          </p>
          <h2 className="mt-3 font-display text-2xl font-medium">
            The control room is wired up.
          </h2>
          <p className="mt-3 text-text-muted">
            Backend, database, and frontend are running. The approval queue,
            bucket view, and policy editor land in later phases.
          </p>

          {/* Signature element preview: the autonomy chip + confidence read */}
          <div className="mt-8 flex items-center gap-3 rounded-control border border-border bg-surface-2 px-4 py-3">
            <span className="rounded-control bg-accent px-2 py-0.5 text-xs font-medium text-bg">
              auto&nbsp;send
            </span>
            <span className="text-sm">Sample drafted reply</span>
            <span className="ml-auto font-mono text-xs text-text-muted">
              0.94
            </span>
          </div>

          {/* Category accents — proves the semantic color tokens resolve */}
          <div className="mt-6 flex flex-wrap gap-2">
            {categories.map((c) => (
              <span
                key={c.label}
                className="flex items-center gap-1.5 font-mono text-xs text-text-muted"
              >
                <span className={`h-2 w-2 rounded-full ${c.cls}`} />
                {c.label}
              </span>
            ))}
          </div>
        </div>
      </main>
    </div>
  )
}

export default App
