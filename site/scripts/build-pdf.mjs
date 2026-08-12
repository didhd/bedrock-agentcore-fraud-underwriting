/**
 * Render the whole documentation site into ONE print-ready PDF.
 *
 * WHY A SCRIPT AND NOT A BROWSER "PRINT TO PDF"
 *
 * Printing 40 pages by hand is 40 chances to miss one, and it loses the reading
 * order that `meta.json` encodes. This walks that file, so the PDF is ordered the
 * way the sidebar is and a new page is picked up automatically.
 *
 * WHAT IT HAS TO WORK AROUND
 *
 *  - **Mermaid renders client-side.** The diagrams are drawn by JS after load, so a
 *    naive fetch-and-print gets 33 empty boxes. Each page is loaded in a real
 *    browser and we wait for the `<svg>` inside every `<figure>` to exist.
 *  - **Dark mode must be off.** The site follows the OS, and a dark PDF wastes ink
 *    and reads badly when printed. `colorScheme: 'light'` is forced.
 *  - **Chrome only emits one PDF per page.** There is no multi-URL mode, so each
 *    page is printed separately and the parts are concatenated afterwards.
 *  - **Site chrome is noise on paper.** The sidebar, the search box, the "On this
 *    page" rail and the Copy/Open buttons are all navigation, useless in print, and
 *    they squeeze the content column to about half the width. They are hidden.
 *
 * Usage:  node scripts/build-pdf.mjs [--base http://localhost:3002] [--out ../media/docs.pdf]
 */

import { chromium } from 'playwright';
import { readFile, mkdir, writeFile, rm } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';

const args = process.argv.slice(2);
const argOf = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback;
};

const BASE = argOf('--base', 'http://localhost:3002');
const OUT = path.resolve(argOf('--out', path.join(import.meta.dirname, '..', '..', 'media', 'engagement-record.pdf')));
const TMP = path.join(import.meta.dirname, '..', '.pdf-parts');

/** Print CSS: strip navigation, widen the content column, avoid mid-element breaks. */
const PRINT_CSS = `
  /* Navigation and page furniture carry no meaning on paper. */
  aside, nav[aria-label], #nd-sidebar, #nd-tocnav, #nd-toc,
  [data-slot="sidebar"], [role="search"], button[aria-label*="Search" i],
  header button, .fd-toc, [data-toc], [aria-label="Table of contents"] { display: none !important; }

  /* Fumadocs' per-page "Copy Markdown" / "Open" control: an affordance for a browser
     that reads as a dead screenshot on paper. It carries no data-slot or stable
     class, so it is matched structurally -- the bordered action row that sits
     directly inside <article>, immediately after the page description. Removing it
     also removes the rule below it, which is why the divider is restored on the
     following sibling. */
  article > div.flex.flex-row.items-center.border-b { display: none !important; }

  /* Reclaim the width the sidebar and TOC rail were using. */
  main, article, [data-slot="content"], .prose {
    max-width: 100% !important; width: 100% !important;
    margin-left: 0 !important; margin-right: 0 !important;
    padding-left: 0 !important; padding-right: 0 !important;
  }
  body { background: #fff !important; }

  /* A diagram or a table split across a page break is unreadable. */
  figure, table, pre { break-inside: avoid; page-break-inside: avoid; }
  /* A heading stranded at the foot of a page is worse than a slightly short page. */
  h1, h2, h3 { break-after: avoid; page-break-after: avoid; }

  /* NOTE: deliberately no page-break-before on h1. Each document is printed as its
     own PDF and concatenated, so a break before the title is already guaranteed by
     the concatenation -- adding it here emitted a blank leading page on all 40. */

  /* Fumadocs renders a sticky page header; in print it becomes a floating banner
     that overlaps the first paragraph. Neutralise the positioning rather than
     hiding it, so the site name still appears once at the top. */
  header, [data-slot="header"] { position: static !important; }

  /* Long code lines must wrap rather than clip at the paper edge. */
  pre, code { white-space: pre-wrap !important; word-break: break-word !important; }

  /* Callout borders survive; their background tints do not print usefully. */
  * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
`;

async function pageOrder() {
  const meta = JSON.parse(
    await readFile(path.join(import.meta.dirname, '..', 'content', 'docs', 'meta.json'), 'utf8'),
  );
  return meta.pages
    .filter((p) => !p.startsWith('---'))
    // `foo/index` is served at `/docs/foo`, and `index` at `/docs`.
    .map((p) => (p === 'index' ? '' : p.replace(/\/index$/, '')));
}

async function main() {
  const slugs = await pageOrder();
  console.log(`rendering ${slugs.length} pages from ${BASE}`);

  await rm(TMP, { recursive: true, force: true });
  await mkdir(TMP, { recursive: true });
  await mkdir(path.dirname(OUT), { recursive: true });

  const browser = await chromium.launch();
  const context = await browser.newContext({
    colorScheme: 'light', // never print the dark palette
    viewport: { width: 1280, height: 1600 },
  });
  const page = await context.newPage();

  const parts = [];
  const problems = [];

  for (const [i, slug] of slugs.entries()) {
    const url = slug ? `${BASE}/docs/${slug}` : `${BASE}/docs`;
    const label = slug || 'index';

    const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 60_000 });
    if (!response || response.status() !== 200) {
      problems.push(`${label}: HTTP ${response ? response.status() : 'no response'}`);
      continue;
    }

    // Wait for every <figure> to contain a rendered <svg>. Mermaid draws these
    // after hydration, so printing before this yields empty boxes.
    const figureCount = await page.locator('figure').count();
    if (figureCount > 0) {
      try {
        await page.waitForFunction(
          () => {
            const figs = [...document.querySelectorAll('figure')];
            return figs.length > 0 && figs.every((f) => f.querySelector('svg'));
          },
          { timeout: 20_000 },
        );
      } catch {
        const rendered = await page.locator('figure svg').count();
        problems.push(`${label}: ${rendered}/${figureCount} diagrams rendered`);
      }
    }

    await page.addStyleTag({ content: PRINT_CSS });
    await page.emulateMedia({ media: 'print' });
    await page.waitForTimeout(400); // let the print stylesheet settle

    const file = path.join(TMP, `${String(i).padStart(2, '0')}-${label.replace(/\//g, '_')}.pdf`);
    await page.pdf({
      path: file,
      format: 'A4',
      printBackground: true,
      margin: { top: '16mm', bottom: '18mm', left: '14mm', right: '14mm' },
      displayHeaderFooter: true,
      headerTemplate: '<div></div>',
      footerTemplate:
        '<div style="width:100%;font-size:8px;color:#64748b;padding:0 14mm;' +
        'display:flex;justify-content:space-between;font-family:system-ui,sans-serif">' +
        '<span>Fraud underwriting on Amazon Bedrock AgentCore — engagement record</span>' +
        '<span class="pageNumber"></span></div>',
    });
    parts.push(file);
    console.log(`  [${i + 1}/${slugs.length}] ${label}${figureCount ? ` (${figureCount} diagrams)` : ''}`);
  }

  await browser.close();

  if (problems.length) {
    console.log('\nWARNINGS:');
    for (const p of problems) console.log(`  ${p}`);
  }

  await writeFile(path.join(TMP, 'parts.txt'), parts.join('\n'), 'utf8');
  console.log(`\n${parts.length} parts in ${TMP}`);
  console.log(`concatenate into: ${OUT}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
