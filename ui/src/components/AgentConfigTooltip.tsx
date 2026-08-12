/**
 * What an agent is actually configured with, on hover.
 *
 * The question this answers is "is thinking on for this agent?", and before this existed
 * it was answerable only by constructing a model object in Python and inspecting
 * `config["additional_request_fields"]`. That is the wrong place for an operator to look
 * and a worse place for a customer to have to trust.
 *
 * Every value comes from `agents/agent_config.py`, which READS the modules that build the
 * agent rather than restating them, so the tooltip cannot drift from what runs.
 *
 * WHAT IS DELIBERATELY NOT HERE: the prompt text. It is customer-confidential
 * (`prompts/verbatim/` is git-ignored for that reason) and this component renders in a
 * browser on a public CloudFront URL. So the prompt is described -- which file, how many
 * characters, whether its hash verified -- and never quoted. A tooltip that leaked a
 * customer prompt would be a worse problem than the one it solves.
 */

import { Badge } from "@/components/ui/badge"
import { EM_DASH, integer } from "@/lib/format"
import type { AgentRuntimeConfig } from "@/lib/types"

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex items-baseline justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-mono text-[0.6875rem]">{children}</span>
    </span>
  )
}

export function AgentConfigTooltip({
  config,
  model,
  rateLabel,
}: {
  config: AgentRuntimeConfig | null
  model: string | null
  rateLabel: string
}) {
  // No config is a real state, not an omission: the reporter degrades to null rather
  // than taking the panel down, so say which fact is missing instead of showing blanks.
  if (!config) {
    return (
      <span className="flex flex-col gap-1">
        <span className="font-mono">{model ?? EM_DASH}</span>
        <span>Rate: {rateLabel}</span>
        <span className="text-muted-foreground">
          Per-agent configuration was not reported by this backend.
        </span>
      </span>
    )
  }

  const { reasoning, prompt, prompt_cache: cache } = config

  return (
    <span className="flex w-72 flex-col gap-2 text-xs">
      <span className="flex flex-col gap-0.5">
        <span className="font-mono text-[0.6875rem] break-all">{config.model_id}</span>
        <span className="text-muted-foreground">Rate: {rateLabel}</span>
      </span>

      <span className="flex flex-col gap-1 border-t pt-1.5">
        <Row label="Tier">{config.tier}</Row>
        <Row label="Max output tokens">{integer(config.max_output_tokens)}</Row>
        <Row label="Thinking / reasoning">
          <Badge
            variant={reasoning.enabled ? "default" : "secondary"}
            className="font-mono text-[0.625rem]"
          >
            {reasoning.enabled ? `on · effort ${reasoning.effort}` : "off"}
          </Badge>
        </Row>
        <Row label="Transport">{reasoning.transport}</Row>
      </span>

      {/* The reason, not just the state. "off" invites "is that a bug?", and the answer
          is in the code that builds the agent, so it travels with the value. */}
      <span className="border-t pt-1.5 leading-relaxed text-muted-foreground">
        {reasoning.enabled
          ? `Set via ${reasoning.request_field}.`
          : `No ${reasoning.request_field} is set, so the model runs at its own default.`}
      </span>

      <span className="flex flex-col gap-1 border-t pt-1.5">
        <Row label="Prompt">{prompt.source.replace("prompts/verbatim/", "")}</Row>
        <Row label="Prompt size">
          {integer(prompt.chars)} chars · ~{integer(prompt.estimated_tokens)} tok
        </Row>
        {prompt.orchestration_notes_chars ? (
          <Row label="Orchestration notes">
            {integer(prompt.orchestration_notes_chars)} chars
          </Row>
        ) : null}
        <Row label="Verbatim">
          <Badge variant="secondary" className="font-mono text-[0.625rem]">
            {prompt.sha256_verified ? "sha256 verified" : "unverified"}
          </Badge>
        </Row>
      </span>

      {/* Prompt caching is the one place a plausible-looking optimisation silently does
          nothing: below the model's minimum prefix the cachePoint is accepted, reports
          cacheWriteInputTokens=0, and every call pays full price. So the verdict is shown
          rather than assumed. */}
      {cache && typeof cache.will_cache === "boolean" ? (
        <span className="flex flex-col gap-1 border-t pt-1.5">
          <Row label="Prompt cache">
            <Badge
              variant={cache.will_cache ? "default" : "secondary"}
              className="font-mono text-[0.625rem]"
            >
              {cache.will_cache ? "engages" : "does not engage"}
            </Badge>
          </Row>
          {cache.prompt_tokens != null && cache.min_cacheable_tokens != null ? (
            <Row label="Prefix vs minimum">
              ~{integer(cache.prompt_tokens)} / {integer(cache.min_cacheable_tokens)} tok
            </Row>
          ) : null}
        </span>
      ) : null}

      <span className="border-t pt-1.5 leading-relaxed text-muted-foreground">
        Fresh agent per invocation · {config.conversation_manager}
      </span>
    </span>
  )
}
