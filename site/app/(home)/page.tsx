import Link from 'next/link';

const headlines: { label: string; value: string; note: string }[] = [
  {
    label: 'End-to-end adjudication',
    value: '31.45 s p50',
    note: '46.55 s p95, n=42 live runs',
  },
  {
    label: 'Under the 60 s target',
    value: '40 of 42',
    note: '12 of 14 applications on every repetition',
  },
  {
    label: 'Cost per adjudication',
    value: '$0.1440 p50',
    note: 'n=41 fully priced runs',
  },
  {
    label: 'Blind-judged analyses',
    value: '168',
    note: '48 tier study + 120 cross-family, two judges',
  },
];

export default function HomePage() {
  return (
    <main className="flex flex-1 flex-col items-center px-4 py-16">
      <div className="w-full max-w-3xl">
        <p className="text-sm font-medium uppercase tracking-widest text-fd-muted-foreground">
          Engagement record
        </p>
        <h1 className="mt-3 text-3xl font-bold sm:text-4xl">
          Point Predictive on Amazon Bedrock AgentCore
        </h1>
        <p className="mt-4 text-fd-muted-foreground">
          A 1:1 port of eight specialist fraud agents plus a master synthesizer, off Snowflake
          Intelligence and onto Amazon Bedrock AgentCore. Every number on this site traces to a
          committed artifact, and the page that quotes it names the file.
        </p>

        <dl className="mt-10 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {headlines.map((h) => (
            <div key={h.label} className="rounded-lg border bg-fd-card p-4">
              <dt className="text-sm text-fd-muted-foreground">{h.label}</dt>
              <dd className="mt-1 text-2xl font-semibold">{h.value}</dd>
              <dd className="mt-1 text-xs text-fd-muted-foreground">{h.note}</dd>
            </div>
          ))}
        </dl>

        <div className="mt-10 flex flex-col gap-3 sm:flex-row">
          <Link
            href="/docs/executive-summary"
            className="rounded-lg bg-fd-primary px-4 py-2 text-center text-sm font-medium text-fd-primary-foreground"
          >
            Executive summary
          </Link>
          <Link
            href="/docs/fidelity/prompt-fidelity"
            className="rounded-lg border px-4 py-2 text-center text-sm font-medium"
          >
            Engineering: prompt fidelity
          </Link>
          <Link href="/docs" className="rounded-lg border px-4 py-2 text-center text-sm font-medium">
            All pages
          </Link>
        </div>

        <p className="mt-10 text-xs text-fd-muted-foreground">
          Percentiles carry their sample count. No p99 appears anywhere on this site: the harness
          refuses one below 100 samples and the largest benchmark here is n=42.
        </p>
      </div>
    </main>
  );
}
