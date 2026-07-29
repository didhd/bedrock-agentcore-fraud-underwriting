'use client';

import { useEffect, useId, useRef, useState } from 'react';

/**
 * A Mermaid diagram, rendered client-side.
 *
 * Mermaid is ~500KB and touches `document` at import time, so it cannot be part
 * of the server bundle: it is imported dynamically inside the effect. Until it
 * resolves the block renders nothing rather than raw diagram source, which would
 * otherwise flash on every page load.
 *
 * Theme follows the site's light/dark mode by reading the `dark` class Fumadocs
 * puts on `<html>`, and re-renders when it changes -- a diagram left on the light
 * palette in dark mode is unreadable, which is worse than no diagram.
 */
export function Mermaid({ chart, title }: { chart: string; title?: string }) {
  const id = useId().replace(/:/g, '');
  const container = useRef<HTMLDivElement>(null);
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const root = document.documentElement;
    const read = () => setDark(root.classList.contains('dark'));
    read();
    const observer = new MutationObserver(read);
    observer.observe(root, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      const { default: mermaid } = await import('mermaid');
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: dark ? 'dark' : 'neutral',
        fontFamily: 'var(--font-sans, ui-sans-serif, system-ui, sans-serif)',
        themeVariables: {
          fontSize: '14px',
          // Keep the palette close to the site's own surfaces so a diagram reads
          // as part of the page rather than as a pasted image.
          primaryColor: dark ? '#1e293b' : '#f1f5f9',
          primaryBorderColor: dark ? '#475569' : '#94a3b8',
          primaryTextColor: dark ? '#e2e8f0' : '#0f172a',
          lineColor: '#64748b',
        },
        flowchart: { curve: 'basis', htmlLabels: true, padding: 12 },
        sequence: { useMaxWidth: true },
      });

      try {
        const { svg } = await mermaid.render(`m${id}`, chart.trim());
        if (!cancelled && container.current) container.current.innerHTML = svg;
      } catch (error) {
        // A malformed diagram must not blank the page it is on. Show the source
        // so the error is fixable by whoever is reading, and log the cause.
        console.error('mermaid render failed', error);
        if (!cancelled && container.current) {
          container.current.innerHTML = `<pre class="text-xs overflow-x-auto">${chart
            .trim()
            .replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' })[c] as string)}</pre>`;
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [chart, dark, id]);

  return (
    <figure className="my-6 overflow-x-auto rounded-lg border bg-fd-card p-4">
      <div ref={container} className="flex justify-center [&_svg]:h-auto [&_svg]:max-w-full" />
      {title ? (
        <figcaption className="mt-3 text-center text-sm text-fd-muted-foreground">{title}</figcaption>
      ) : null}
    </figure>
  );
}
