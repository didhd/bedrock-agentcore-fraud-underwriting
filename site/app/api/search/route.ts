/**
 * Search index for the docs site.
 *
 * `staticGET`, not `GET`, and that choice is what makes the whole site deployable
 * to S3 + CloudFront. `createFromSource` returns both: `GET` runs Orama on a
 * server and answers one query per request, while `staticGET` emits the entire
 * index once at build time as a plain JSON file the browser downloads and queries
 * locally. With `output: 'export'` the server variant is not just unnecessary, it
 * is fatal -- `next build` fails with
 *
 *   export const dynamic = "force-static"/export const revalidate not configured
 *   on route "/api/search" with "output: export"
 *
 * because a route handler that reads the request cannot be prerendered.
 *
 * The client half is not optional. `app/layout.tsx` must pass
 * `search={{ options: { type: 'static' } }}` to `RootProvider`, or the dialog goes
 * on POSTing to this path, receives the index JSON instead of a result list, and
 * renders "no results" for every query -- a search box that looks like it works.
 */

import { source } from '@/lib/source';
import { createFromSource } from 'fumadocs-core/search/server';

export const revalidate = false;

export const { staticGET: GET } = createFromSource(source, {
  // https://docs.orama.com/docs/orama-js/supported-languages
  language: 'english',
});
