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
 *
 * SECURITY: the rendered SVG is inserted by PARSING it into nodes and appending
 * them, never by assigning raw markup to an element. Mermaid's output is markup
 * built from `chart`, so writing it as HTML is a script-injection sink; a security
 * scan flags it, and correctly. Two controls apply instead:
 *
 *   1. `DOMPurify.sanitize` with `USE_PROFILES: {svg, svgFilters}` strips scripts,
 *      event handlers and foreign elements while leaving legitimate SVG intact.
 *   2. The sanitized markup is turned into real nodes via `DOMParser` and moved in
 *      with `replaceChildren`, so no HTML-parsing sink is used at all.
 *
 * Mermaid's own `securityLevel: 'strict'` is kept as a third layer. Defence in
 * depth matters here because diagram text on this site is author-controlled today
 * but the component is generic and the next caller may not be.
 */
/**
 * Repair label text that mermaid paints with the shape's own fill.
 *
 * Mermaid's generated stylesheet contains rules like
 * `.actor { fill: #eee; stroke: ... }` intended for the BOX, but it also puts
 * `class="actor"` on the `<text>` inside that box. With `htmlLabels: true` the label
 * is a `<foreignObject>` and never matches, so the bug is invisible. We must keep
 * `htmlLabels: false` for the sanitiser (foreignObject is stripped), which exposes it:
 * every label became near-white text on a near-white box — shapes and arrows drawn
 * correctly, and not one word readable.
 *
 * Fixed with an inline `style` declaration, NOT a `fill` attribute. That distinction
 * is the whole fix: a presentation attribute (`fill="#0f172a"`) sits BELOW author CSS
 * in the cascade, so mermaid's `.actor { fill: #eee }` still wins — measured, the
 * attribute was applied and `getComputedStyle` still returned `rgb(238,238,238)`.
 * An inline style declaration outranks it.
 *
 * Styling is set property-by-property through the CSSStyleDeclaration API; no markup
 * is parsed and no string is assigned to `style`, so this adds no injection surface.
 */
function textFillFix(svg: Element, foreground: string) {
  svg.querySelectorAll<SVGElement>('text, tspan').forEach((node) => {
    // Leave anything mermaid coloured deliberately (edge labels, class diagram
    // annotations) alone; only override where the fill came from a shape rule.
    const explicit = node.getAttribute('fill');
    if (explicit && explicit !== 'none') return;
    // `sequenceNumber` is the digit inside an autonumber bullet, and that bullet is a
    // dark filled circle in BOTH themes -- so it needs light text regardless of the
    // page theme. A blanket override made those digits dark-on-dark: the labels became
    // readable and the step numbers disappeared instead, which is the failure this
    // whole function exists to prevent, just moved somewhere less obvious.
    if (node.classList.contains('sequenceNumber')) {
      node.style.setProperty('fill', '#ffffff');
      return;
    }
    node.style.setProperty('fill', foreground);
  });
}

/**
 * Rewrite `<br/>` line breaks into mermaid's text-mode escape.
 *
 * With `htmlLabels: true` a label is HTML, so `<br/>` works. We must run
 * `htmlLabels: false` so DOMPurify's SVG profile does not strip the labels
 * (foreignObject is not in the SVG allowlist), and in text mode `<br/>` is not a line
 * break -- it is not markup at all. Mermaid renders NOTHING for such a label, so every
 * multi-line node became an empty box: shapes and arrows drawn, four nodes, one label.
 *
 * The replacement is a REAL newline, not the two characters backslash-n. Mermaid's
 * text-mode parser treats an actual line break inside a quoted label as a line break;
 * emitting `\\n` instead just put those two characters into the label, which rendered
 * as one run-together string (`Master synthesizerGPT-5.6 Luna...`) because the parser
 * dropped them and joined the parts.
 *
 * Done here rather than by editing 155 occurrences across the content tree, so authors
 * can keep writing `<br/>` and the component stays the single place that knows which
 * label mode is in force.
 */
function toTextLabels(chart: string): string {
  return chart.trim().replace(/<br\s*\/?>/gi, '\n');
}

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

    /**
     * Parse sanitised SVG into real nodes and swap them in.
     *
     * `image/svg+xml` yields an XML document whose `documentElement` IS the `<svg>`
     * -- there is no `body` to read, unlike an HTML parse. Importing the
     * documentElement is therefore both correct and the narrower thing to do: a
     * parser error surfaces as a `parsererror` element, which is checked rather
     * than silently appended as broken markup.
     */
    const setSvg = (markup: string) => {
      const target = container.current;
      if (!target) return false;
      const parsed = new DOMParser().parseFromString(markup, 'image/svg+xml');
      const root = parsed.documentElement;
      if (!root || root.getElementsByTagName('parsererror').length || root.nodeName === 'parsererror') {
        return false;
      }
      const imported = document.importNode(root, true);
      textFillFix(imported, dark ? '#e2e8f0' : '#0f172a');
      target.replaceChildren(imported);
      return true;
    };

    void (async () => {
      const [{ default: mermaid }, { default: DOMPurify }] = await Promise.all([
        import('mermaid'),
        import('dompurify'),
      ]);

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
        // htmlLabels MUST be false, and it MUST be set at the TOP LEVEL as well as
        // per-diagram. With it on, mermaid renders label text inside <foreignObject>
        // (i.e. real HTML), which DOMPurify's SVG profile removes -- producing boxes
        // with no text at all. Setting only `flowchart.htmlLabels` is not enough: the
        // top-level default still applied and every flowchart node rendered as an
        // empty rectangle while the sequence diagram (which has no such default) was
        // fine. That asymmetry is what made the bug look like a styling problem.
        htmlLabels: false,
        flowchart: { curve: 'basis', htmlLabels: false, padding: 12 },
        sequence: { useMaxWidth: true },
      });

      try {
        const { svg } = await mermaid.render(`m${id}`, toTextLabels(chart));
        if (cancelled) return;
        const clean = DOMPurify.sanitize(svg, {
          USE_PROFILES: { svg: true, svgFilters: true },
        });
        if (!setSvg(clean)) throw new Error('sanitised SVG did not parse');
      } catch (error) {
        // A malformed diagram must not blank the page it is on. Show the source as
        // TEXT -- textContent cannot execute anything, so no sanitiser is needed.
        console.error('mermaid render failed', error);
        if (cancelled) return;
        const target = container.current;
        if (!target) return;
        const pre = document.createElement('pre');
        pre.className = 'text-xs overflow-x-auto';
        pre.textContent = chart.trim();
        target.replaceChildren(pre);
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
