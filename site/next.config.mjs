import { createMDX } from 'fumadocs-mdx/next';

const withMDX = createMDX();

/**
 * @type {import('next').NextConfig}
 *
 * `output: 'export'` because this site is deployed to S3 behind CloudFront, with no
 * Node process anywhere. Everything the site needs is prerendered at build time
 * into `out/`: the 40 doc pages, the OG images under /og/docs/**, the llms.txt /
 * llms-full.txt / llms.mdx ingestion routes, and the search index.
 *
 * Two things had to change for this to build, and both are load-bearing:
 *
 *  - `app/api/search/route.ts` exports Fumadocs' `staticGET` rather than `GET`, so
 *    the search index is emitted once as JSON instead of being queried per
 *    request. Without it `next build` fails outright on that route.
 *  - `app/layout.tsx` passes `search={{ options: { type: 'static' } }}` to
 *    RootProvider, so the client queries that JSON locally. Miss this half and
 *    search silently returns nothing for every query.
 *
 * `images.unoptimized` follows from the same constraint: /_next/image is a runtime
 * endpoint, and there is no runtime. `next dev` is unaffected -- `output` applies
 * to `next build` only -- so the local docs server on :3002, the PDF builder and
 * the demo recorder all keep working unchanged.
 */
const config = {
  reactStrictMode: true,
  output: 'export',
  images: { unoptimized: true },
};

export default withMDX(config);
