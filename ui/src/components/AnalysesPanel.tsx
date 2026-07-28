/**
 * The specialist analyses exactly as the customer's own pipeline returns them.
 *
 * This is the fidelity panel. Three things it must get right, because they are
 * what the prompt author will check first:
 *
 *  1. The TITLE. Six domains define one ("- Title: Identity Fraud Risk Analysis").
 *     bustout and rings define NONE -- their prompts say "Return only the final
 *     analysis." / "Return only final analysis." -- so their entries render with no
 *     title and say so explicitly. Inventing a title for them would be a visible
 *     fidelity break.
 *  2. TITLED PROSE, not key-value. The analysis is rendered as paragraphs.
 *  3. The LENGTH BUDGET, checked and displayed: LOW RISK is 3-5 sentences;
 *     POSSIBLE/HIGH is capped at 4000 characters; straw with no co-borrower is
 *     2-3 sentences.
 *
 * The budget check reports what the text actually is. It does not clamp or edit --
 * a violation is information for the prompt author, not something to hide.
 */

import { CircleAlert, CircleCheck, Info } from "lucide-react"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { RiskBadge } from "@/components/atoms"
import { cn } from "@/lib/utils"
import { EM_DASH, integer, ms } from "@/lib/format"
import type { AgentCard, Health } from "@/lib/types"

interface BudgetResult {
  ok: boolean
  rule: string
  actual: string
}

function budgetFor(card: AgentCard, health: Health | null): BudgetResult | null {
  if (card.analysis === null || card.risk_band === null) return null
  const rules = health?.length_rules
  const minLow = rules?.low_risk_min_sentences ?? 3
  const maxLow = rules?.low_risk_max_sentences ?? 5
  const maxChars = rules?.possible_high_max_chars ?? 4000
  const strawMax = rules?.straw_no_coborrower_max_sentences ?? 3

  const sentences = card.sentence_count ?? 0
  const chars = card.char_count ?? 0

  // straw with no co-borrower: the prompt's tighter 2-3 sentence rule. Detected
  // from the analysis text stating the absence, since the card does not carry the
  // application's co-borrower flag.
  if (card.domain === "straw" && /no co-?borrower/i.test(card.analysis)) {
    return {
      ok: sentences >= 2 && sentences <= strawMax,
      rule: `straw, no co-borrower: 2-${strawMax} sentences`,
      actual: `${sentences} sentences`,
    }
  }
  if (card.risk_band === "LOW RISK") {
    return {
      ok: sentences >= minLow && sentences <= maxLow,
      rule: `LOW RISK: ${minLow}-${maxLow} sentences`,
      actual: `${sentences} sentences`,
    }
  }
  return {
    ok: chars <= maxChars,
    rule: `POSSIBLE / HIGH RISK: max ${integer(maxChars)} characters`,
    actual: `${integer(chars)} characters`,
  }
}

function BudgetChip({ budget }: { budget: BudgetResult | null }) {
  if (!budget) return null
  const Icon = budget.ok ? CircleCheck : CircleAlert
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="outline"
          className={cn(
            "gap-1 font-mono text-[0.6875rem]",
            budget.ok
              ? "border-[#0ca30c]/40 text-[#046b04] dark:text-[#4cc94c]"
              : "border-[#fab219]/50 text-[#8a5c00] dark:text-[#fac351]",
          )}
        >
          <Icon aria-hidden />
          {budget.actual}
        </Badge>
      </TooltipTrigger>
      <TooltipContent>
        {budget.ok
          ? `Within the prompt's budget - ${budget.rule}.`
          : `Outside the prompt's budget - ${budget.rule}. Reported, not clamped.`}
      </TooltipContent>
    </Tooltip>
  )
}

/**
 * Render the analysis as the customer receives it. The stored text already carries
 * its title on the first line where the prompt defines one, so the title line is
 * split off and shown as a heading rather than being re-added.
 */
function AnalysisBody({ card }: { card: AgentCard }) {
  const text = card.analysis ?? ""
  const declaredTitle = card.analysis_title
  let body = text
  let renderedTitle: string | null = null

  if (declaredTitle && text.startsWith(declaredTitle)) {
    renderedTitle = declaredTitle
    body = text.slice(declaredTitle.length).replace(/^\s*\n/, "")
  } else if (declaredTitle) {
    // The model did not emit the declared title. Surface the discrepancy instead of
    // silently prepending it.
    renderedTitle = null
  }

  const paragraphs = body
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)

  return (
    <div className="flex flex-col gap-3">
      {declaredTitle === null ? (
        <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
          <Info aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          This agent's prompt defines no output title ("Return only the final analysis"), so none is
          rendered.
        </p>
      ) : renderedTitle ? (
        <h4 className="text-sm font-semibold text-foreground">{renderedTitle}</h4>
      ) : (
        <p className="flex items-start gap-1.5 text-xs text-[#8a5c00] dark:text-[#fac351]">
          <CircleAlert aria-hidden className="mt-0.5 size-3.5 shrink-0" />
          The prompt declares the title "{declaredTitle}" but the returned text does not begin with
          it.
        </p>
      )}
      {paragraphs.length === 0 ? (
        <p className="text-sm text-muted-foreground">{EM_DASH}</p>
      ) : (
        paragraphs.map((paragraph, index) => (
          <p
            key={index}
            className="text-sm leading-relaxed whitespace-pre-wrap text-foreground/90"
          >
            {paragraph}
          </p>
        ))
      )}
    </div>
  )
}

export function AnalysesPanel({
  cards,
  health,
}: {
  cards: AgentCard[]
  health: Health | null
}) {
  const withAnalysis = cards.filter((card) => card.analysis !== null)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Specialist analyses</CardTitle>
        <CardDescription>
          Titled prose, exactly as each agent's own prompt specifies. Six domains declare an output
          title; BRUCE_BUSTOUT and RENEE_RINGS declare none, so none is shown. Each entry reports its
          measured length against the prompt's budget.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {withAnalysis.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
            No analyses yet. Run an application to see what each specialist returns.
          </p>
        ) : (
          <Accordion type="multiple">
            {withAnalysis.map((card) => {
              const budget = budgetFor(card, health)
              return (
                <AccordionItem key={card.domain} value={card.domain}>
                  <AccordionTrigger className="pr-2">
                    <span className="flex flex-1 flex-wrap items-center gap-2 pr-3">
                      <span className="font-mono text-[0.8125rem]">{card.agent_name}</span>
                      <RiskBadge band={card.risk_band} />
                      <BudgetChip budget={budget} />
                      <span className="ml-auto font-mono text-[0.6875rem] text-muted-foreground">
                        {ms(card.latency_ms)}
                      </span>
                    </span>
                  </AccordionTrigger>
                  <AccordionContent>
                    <AnalysisBody card={card} />
                    <Separator className="my-3" />
                    <p className="font-mono text-[0.6875rem] text-muted-foreground">
                      {budget ? `${budget.rule} - actual ${budget.actual}` : ""}
                    </p>
                  </AccordionContent>
                </AccordionItem>
              )
            })}
          </Accordion>
        )}
      </CardContent>
    </Card>
  )
}
