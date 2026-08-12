/**
 * The small subset of Markdown these agents actually emit.
 *
 * Francis's own prompt mandates `## EXECUTIVE SUMMARY` / `## ANALYSIS` and a conditional
 * `###RECOMMENDATIONS`, and the specialists emit `**bold**` labels, `-` bullets and
 * numbered lists. Rendering that as `whitespace-pre-wrap` -- which is what the analyses
 * panel does -- shows literal `##` and `**` to the customer in a chat window.
 *
 * WHY NOT react-markdown
 *
 * It is a real dependency plus a sanitiser for a fixed, tiny grammar. This produces React
 * ELEMENTS from a line-by-line parse and never touches `innerHTML`, so there is no
 * sanitisation step to get wrong -- the same reason the chart component's
 * `dangerouslySetInnerHTML` was removed from this codebase. Anything unrecognised falls
 * through as plain text, which is the correct failure: an unhandled construct looks
 * slightly plain rather than becoming an injection surface.
 *
 * NO RAW HTML IS EVER INTERPRETED. Model output is untrusted input.
 */

import * as React from "react"

/** Split on `**bold**`, keeping the delimiters' content. */
function inline(text: string, keyPrefix: string): React.ReactNode[] {
  const out: React.ReactNode[] = []
  const pattern = /\*\*([^*]+)\*\*|`([^`]+)`/g
  let last = 0
  let match: RegExpExecArray | null
  let index = 0

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index))
    if (match[1] !== undefined) {
      out.push(
        <strong key={`${keyPrefix}-b${index}`} className="font-semibold">
          {match[1]}
        </strong>,
      )
    } else if (match[2] !== undefined) {
      out.push(
        <code
          key={`${keyPrefix}-c${index}`}
          className="rounded bg-muted px-1 py-0.5 font-mono text-[0.8125em]"
        >
          {match[2]}
        </code>,
      )
    }
    last = match.index + match[0].length
    index += 1
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function Markdown({ children }: { children: string }) {
  const blocks: React.ReactNode[] = []
  const lines = children.split("\n")
  let bullets: string[] = []

  const flush = () => {
    if (!bullets.length) return
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="ml-4 flex list-disc flex-col gap-1">
        {bullets.map((item, i) => (
          <li key={i}>{inline(item, `li-${blocks.length}-${i}`)}</li>
        ))}
      </ul>,
    )
    bullets = []
  }

  lines.forEach((raw, i) => {
    const line = raw.trimEnd()

    // `###RECOMMENDATIONS` with no space is exactly how Francis's prompt spells it, so
    // the heading pattern must not require one.
    const heading = /^(#{1,4})\s*(.+)$/.exec(line)
    if (heading) {
      flush()
      const level = heading[1].length
      blocks.push(
        <p
          key={`h-${i}`}
          className={
            level <= 2
              ? "mt-1 text-[0.8125rem] font-semibold tracking-wide uppercase"
              : "mt-1 text-[0.8125rem] font-semibold"
          }
        >
          {inline(heading[2], `h-${i}`)}
        </p>,
      )
      return
    }

    const bullet = /^\s*[-*]\s+(.+)$/.exec(line)
    if (bullet) {
      bullets.push(bullet[1])
      return
    }

    flush()
    if (!line.trim()) return
    blocks.push(
      <p key={`p-${i}`} className="leading-relaxed">
        {inline(line, `p-${i}`)}
      </p>,
    )
  })
  flush()

  return <div className="flex flex-col gap-2">{blocks}</div>
}
