# Engagement documentation site

Fumadocs (Next.js 16 + `fumadocs-mdx`) site documenting the AnyCompany → Amazon Bedrock
AgentCore port. Scaffolded with `npm create fumadocs-app@latest` (template
`+next+fuma-docs-mdx`, no `/src`, no linter).

```bash
npm install
npm run dev     # http://localhost:3000
npm run build   # must pass before any page is considered done
```

## Where content lives

All pages are MDX under `content/docs/`. The navigation tree is `content/docs/meta.json` — a
flat `pages` array using `---Section---` separator entries, so section headings and page order
are both defined in that one file. **Adding a file does not add it to the nav**; add its slug
to `meta.json` too. A slug in `meta.json` with no matching file is ignored silently rather
than failing the build, so a missing page shows up as an absent nav entry.

## MDX components available without an import

Registered in `components/mdx.tsx`: `Callout`, `Card`/`Cards`, `Tabs`/`Tab`,
`Accordions`/`Accordion`, `Steps`/`Step`, `Files`/`File`/`Folder`, `TypeTable`, plus fumadocs'
defaults (code blocks, headings, tables).

`Tabs`/`Tab`, `Steps`, `Accordions`, `Files` and `TypeTable` are **not** fumadocs defaults. A
page using one before it is registered fails at prerender with
``Expected component `Tab` to be defined`` — which is why they are all registered up front.

`Callout` types: `info` | `warn` | `error` | `success` | `warning` | `idea`. Convention on this
site:

- `info` — audience note, context, scope
- `warn` — a caveat, a sample-size limit, a figure that must not be over-read
- `error` — a defect, a refusal, or a blocking open question

## House rules for content

1. **Every number names its artifact.** Cite the file path, e.g.
   `evals/results/pipeline_14x3_us-east-1.json`. If it is not in an artifact, either omit it or
   label it `NOT MEASURED`.
2. **Percentiles carry their `n`.** The harness refuses p95 below 20 samples and p99 below 100
   and records the refusal string in the artifact. Do not convert a refusal into a number. No
   p99 appears anywhere on this site.
3. **Say which audience the page is for** in a leading `Callout type="info"`.
4. **Where two sources disagree, show both and name the authoritative one.**
5. **No customer prompt text.** The nine prompts and their orchestration documents are
   confidential and live in git-ignored `prompts/verbatim/` and in `docs/`. Describe and cite
   them; quote at most a short phrase where a rule's exact wording is the point.
6. No marketing voice. State what was measured, with `n`, and what was not.

## Deployment

`app/layout.tsx` reads `SITE_URL` for `metadataBase`, defaulting to `http://localhost:3000`.
Set it when publishing so OG image URLs resolve.
