/**
 * SSE bridge: browser -> AgentCore Runtime.
 *
 * WHY THIS EXISTS AT ALL
 *
 * CloudFront cannot call the runtime itself. Origin Access Control signs SigV4 for
 * S3, Lambda function URLs and VPC origins -- not for `bedrock-agentcore` -- and a
 * browser has no credentials to sign with either. So exactly one piece of compute
 * is unavoidable, and this is it, kept as small as the job allows: the two static
 * sites are pure S3, and only `/api/stream/*` reaches Lambda.
 *
 * AUTH: THE SDK AND THE EXECUTION ROLE, NOT HAND-ROLLED SIGV4
 *
 * The first version of this file signed the request itself with node:crypto to
 * avoid a bundler. It returned HTTP 403 from the service:
 *
 *   The request signature we calculated does not match the signature you provided.
 *
 * The reason is a rule that is easy to read past: in a SigV4 canonical request,
 * every path segment must be URI-encoded **twice** for every service except S3.
 * The runtime ARN sits in the path as a segment, so it is already
 * `arn%3Aaws%3Abedrock-agentcore%3A...` in the URL and has to appear as
 * `arn%253Aaws%253A...` in the string that gets signed. Encoding it once produces a
 * signature that is wrong in a way nothing local can detect -- it fails only
 * against the real service, and the message points at your secret key rather than
 * at your canonicalisation.
 *
 * So this uses `@aws-sdk/client-bedrock-agentcore`, which canonicalises correctly
 * and picks credentials up from the execution role through the default provider
 * chain. No keys, no tokens, nothing to rotate, and the whole class of signing bug
 * is gone rather than fixed. The cost is a bundler, which `NodejsFunction` in the
 * CDK stack runs with esbuild -- emitting **CommonJS**, not ESM. Bundling this file
 * to ESM produced a runtime failure on the very first import:
 *
 *   Dynamic require of "node:https" is not supported
 *     at node_modules/@smithy/node-http-handler/...
 *
 * The SDK ships CJS, its HTTP handler requires node:https lazily, and esbuild's ESM
 * output replaces `require` with a shim that throws on anything it could not resolve
 * statically. Emitting CJS keeps `require` real. This file stays written in ESM
 * syntax; only the output format changes.
 *
 * WHY IT IS NOT A PASSTHROUGH
 *
 * The repo has two SSE frame vocabularies and they are not the same:
 *
 *   app/underwriter/main.py  start | agent_start | agent_done | agent_failed |
 *                            synthesis_start | done
 *   ui/server.py             run_started | agent_started | agent_completed |
 *                            agent_failed | synthesis_started |
 *                            synthesis_completed | run_completed
 *
 * `ui/src/hooks/useRun.ts` consumes the second one and its reducer `default`s to
 * ignoring anything else -- so a naive proxy produces a UI that connects, streams,
 * and displays nothing at all. Translating here rather than changing either side
 * keeps the runtime contract (what the customer deploys) and the UI contract (what
 * the customer watches) both untouched.
 *
 * WHAT IT DELIBERATELY DOES NOT DO
 *
 * It invents nothing. Fields the runtime did not report are forwarded as null, not
 * defaulted to 0 -- a false `cost_usd: 0` would read as "this call was free"
 * instead of "this call's usage was unavailable". The three static-shaped fields
 * the UI needs and the runtime does not send (`rate`, `analysis_title`, and the
 * synthesizer's rate) come from bootstrap.json, generated at build time by
 * deploy/cdk/scripts/build-static-api.py from agents/pricing.py and
 * agents/models.py, so no rate card is retyped in JavaScript.
 */

import crypto from 'node:crypto';
import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from '@aws-sdk/client-bedrock-agentcore';

import BOOTSTRAP from './bootstrap.json';

const RUNTIME_ARN = process.env.RUNTIME_ARN;
const RUNTIME_REGION = process.env.RUNTIME_REGION;
const QUALIFIER = process.env.RUNTIME_QUALIFIER ?? 'DEFAULT';

// One client for the container's lifetime. Credentials come from the execution
// role and the provider chain refreshes them, so there is nothing to rebuild
// per invocation.
const client = new BedrockAgentCoreClient({ region: RUNTIME_REGION });

// ---------------------------------------------------------------------------
// Vocabulary translation
// ---------------------------------------------------------------------------

/** Sentence count, which the runtime does not report but the UI displays. */
function sentenceCount(text) {
  if (typeof text !== 'string' || !text) return null;
  const matches = text.match(/[^.!?]+[.!?]+(\s|$)/g);
  return matches ? matches.length : null;
}

const nullable = (value) => (value === undefined ? null : value);

const specOf = (domain) => BOOTSTRAP.agents.find((a) => a.domain === domain) ?? {};

/**
 * Translate one runtime frame into zero or more UI frames.
 *
 * `done` fans out to TWO frames because the runtime combines what the UI keeps
 * apart: it carries the adjudication (the UI's `synthesis_completed`) and the run's
 * timing and cost roll-up (the UI's `run_completed`) in a single terminal object.
 */
function translate(frame) {
  switch (frame.event) {
    case 'start':
      return [
        {
          event: 'run_started',
          application_id: frame.application_id,
          session_id: frame.session_id,
          backend: 'agentcore-runtime',
          mock_mode: frame.mock_mode,
          measured_tokens: frame.measures_tokens,
          execution_path:
            'AgentCore Runtime (bedrock-agentcore:InvokeAgentRuntime) -> ' +
            'agents.fanout.fan_out_streaming + agents.synthesize.synthesize',
          synthesizer_model: BOOTSTRAP.synthesizer_model,
          agents: (frame.domains ?? []).map((domain) => {
            const spec = specOf(domain);
            return {
              domain,
              agent_name: spec.agent_name ?? null,
              model: spec.model ?? null,
              tier: spec.tier ?? null,
              rate: spec.rate ?? null,
              analysis_title: spec.analysis_title ?? null,
            };
          }),
        },
      ];

    case 'agent_start':
      return [{ event: 'agent_started', domain: frame.domain, at_ms: frame.at_ms }];

    case 'agent_done':
      return [
        {
          event: 'agent_completed',
          domain: frame.domain,
          agent_name: frame.agent_name,
          analysis: frame.analysis,
          analysis_title: nullable(frame.analysis_title),
          risk_band: nullable(frame.risk_band),
          char_count: nullable(frame.char_count),
          sentence_count: sentenceCount(frame.analysis),
          model: nullable(frame.model),
          tier: nullable(frame.tier),
          rate: specOf(frame.domain).rate ?? null,
          latency_ms: nullable(frame.latency_ms),
          model_latency_ms: nullable(frame.model_latency_ms),
          finished_at_ms: nullable(frame.finished_at_ms),
          measured: nullable(frame.measured),
          source: nullable(frame.source),
          input_tokens: nullable(frame.input_tokens),
          output_tokens: nullable(frame.output_tokens),
          cache_read_tokens: nullable(frame.cache_read_tokens),
          cache_write_tokens: nullable(frame.cache_write_tokens),
          cost_usd: nullable(frame.cost_usd),
          stop_reason: nullable(frame.stop_reason),
          prompt_cache: nullable(frame.prompt_cache),
        },
      ];

    case 'agent_failed':
      return [
        {
          event: 'agent_failed',
          domain: frame.domain,
          agent_name: frame.agent_name,
          error: frame.error,
        },
      ];

    case 'synthesis_start':
      return [
        {
          event: 'synthesis_started',
          model: frame.model,
          tier: frame.tier,
          analyses_received: nullable(frame.analyses_received),
          analyses_missing: nullable(frame.analyses_missing),
          at_ms: nullable(frame.at_ms),
        },
      ];

    case 'done': {
      const synthesis = frame.synthesis ?? {};
      const timing = frame.timing ?? {};
      const cost = frame.cost ?? {};
      return [
        {
          event: 'synthesis_completed',
          adjudication: frame.adjudication,
          key_check: frame.key_check,
          model: nullable(synthesis.model),
          tier: nullable(synthesis.tier),
          rate: BOOTSTRAP.synthesizer_rate ?? null,
          latency_ms: nullable(synthesis.latency_ms),
          model_latency_ms: nullable(synthesis.model_latency_ms),
          measured: nullable(synthesis.measured),
          source: nullable(synthesis.source),
          repair_attempts: nullable(synthesis.repair_attempts),
          degraded_domains: nullable(synthesis.degraded_domains),
          input_tokens: nullable(synthesis.input_tokens),
          output_tokens: nullable(synthesis.output_tokens),
          cache_read_tokens: nullable(synthesis.cache_read_tokens),
          cache_write_tokens: nullable(synthesis.cache_write_tokens),
          cost_usd: nullable(synthesis.cost_usd),
        },
        {
          event: 'run_completed',
          fanout_wall_ms: nullable(timing.fanout_wall_ms),
          synthesis_ms: nullable(timing.synthesis_ms),
          end_to_end_ms: nullable(timing.end_to_end_ms),
          slowest_agent: nullable(timing.slowest_agent),
          slowest_agent_ms: nullable(timing.slowest_agent_ms),
          sum_of_agent_ms: nullable(timing.sum_of_agent_ms),
          agent_cost_usd: nullable(cost.agents_usd),
          synthesis_cost_usd: nullable(cost.synthesis_usd),
          cost_per_application_usd: nullable(cost.total_usd),
          specialists_succeeded: nullable(frame.specialists_succeeded),
          specialists_expected: nullable(frame.specialists_expected),
          source: nullable(timing.source),
        },
      ];
    }

    case 'error':
      // The runtime turns a mid-stream exception into a terminal `error` frame
      // AFTER HTTP 200, so a 200 is not evidence of a healthy run. Passed through
      // under the name the UI's reducer checks first.
      return [{ event: 'error', error: frame.error, error_type: nullable(frame.error_type) }];

    default:
      // Anything added later. Forwarded verbatim rather than dropped: the UI
      // reducer ignores what it does not recognise, and silently swallowing frames
      // here would hide a future contract change.
      return [frame];
  }
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------

const APP_ID = /^APP-\d{3,6}$/i;

export const handler = awslambda.streamifyResponse(async (event, responseStream) => {
  const rawPath = event.rawPath ?? '';

  responseStream = awslambda.HttpResponseStream.from(responseStream, {
    statusCode: 200,
    headers: {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-cache, no-store',
      // Belongs on the response, not on a CloudFront ResponseHeadersPolicy, which
      // rejects this header by name.
      'x-accel-buffering': 'no',
    },
  });

  const write = (obj) => responseStream.write(`data: ${JSON.stringify(obj)}\n\n`);

  try {
    const match = rawPath.match(/\/api\/stream\/([^/?]+)/);
    if (!match) {
      write({ event: 'error', error: `no application id in path ${JSON.stringify(rawPath)}` });
      return;
    }
    const appId = decodeURIComponent(match[1]).toUpperCase();
    if (!APP_ID.test(appId)) {
      // Validated before anything is invoked: the id goes into a request body, and
      // an id-shaped allowlist is cheaper than reasoning about what the runtime
      // does with an arbitrary string.
      write({ event: 'error', error: `not a valid application id: ${appId}` });
      return;
    }

    const response = await client.send(
      new InvokeAgentRuntimeCommand({
        agentRuntimeArn: RUNTIME_ARN,
        qualifier: QUALIFIER,
        // AgentCore enforces a 33-character minimum on runtimeSessionId.
        // randomUUID() is 36 with its hyphens; a hex uuid4 would be 32 and rejected.
        runtimeSessionId: crypto.randomUUID(),
        contentType: 'application/json',
        accept: 'text/event-stream',
        payload: new TextEncoder().encode(JSON.stringify({ application_id: appId })),
      })
    );

    const decoder = new TextDecoder();
    let buffer = '';

    for await (const chunk of response.response) {
      buffer += decoder.decode(chunk, { stream: true });
      // SSE frames are separated by a blank line. The tail is held back: a frame
      // split across two chunks must not be parsed as two broken frames.
      let split;
      while ((split = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const line = raw.split('\n').find((l) => l.startsWith('data: '));
        if (!line) continue;
        let frame;
        try {
          frame = JSON.parse(line.slice(6));
        } catch {
          continue;
        }
        for (const out of translate(frame)) write(out);
      }
    }
  } catch (error) {
    write({
      event: 'error',
      error: `${error.name}: ${error.message}`,
      error_type: error.name,
    });
  } finally {
    responseStream.end();
  }
});
