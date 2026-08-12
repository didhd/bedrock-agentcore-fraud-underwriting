import { RootProvider } from 'fumadocs-ui/provider/next';
import './global.css';
import { Inter } from 'next/font/google';
import type { Metadata } from 'next';
import { appName } from '@/lib/shared';

const inter = Inter({
  subsets: ['latin'],
});

// Set so `next build` stops warning that it cannot resolve OG image URLs.
// Override with SITE_URL when this is published somewhere other than localhost.
export const metadata: Metadata = {
  metadataBase: new URL(process.env.SITE_URL ?? 'http://localhost:3000'),
  title: { default: appName, template: `%s — ${appName}` },
  description:
    'Engagement record: a 1:1 port of eight specialist fraud agents plus a master synthesizer onto Amazon Bedrock AgentCore. Every figure traces to a committed artifact.',
};

export default function Layout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="en" className={inter.className} suppressHydrationWarning>
      <body className="flex flex-col min-h-screen">
        {/*
          `type: 'static'` is the client half of app/api/search/route.ts, which
          exports Fumadocs' `staticGET` so the search index is a build-time JSON
          file rather than a server route. Both halves have to agree: leave the
          default here and the dialog keeps querying the endpoint per keystroke,
          gets handed the whole index instead of a result list, and reports "no
          results" for everything -- a search box that fails while looking healthy.
          This is what lets the site deploy to S3 + CloudFront with no compute.
        */}
        <RootProvider search={{ options: { type: 'static' } }}>{children}</RootProvider>
      </body>
    </html>
  );
}
