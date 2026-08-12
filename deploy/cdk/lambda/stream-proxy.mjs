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
 * synthesizer's tier) come from bootstrap.json, generated at build time by
 * deploy/cdk/scripts/build-static-api.py from agents/pricing.py and
 * agents/models.py, so no rate card is retyped in JavaScript.
 *
 * NO SDK, NO DEPENDENCIES
 *
 * SigV4 is ~40 lines of node:crypto. The alternative is bundling
 * @aws-sdk/client-bedrock-agentcore, which is not in the Node 22 runtime's
 * built-in SDK set, so it would mean a build step and a version to keep current
 * for one signed POST.
 */

import crypto from 'node:crypto';
import { readFile } from 'node:fs/promises';

const REGION = process.env.RUNTIME_REGION;
const RUNTIME_ARN = process.env.RUNTIME_ARN;
const QUALIFIER = process.env.RUNTIME_QUALIFIER ?? 'DEFAULT';
const SERVICE = 'bedrock-agentcore';

/** Static per-model facts (rates, tiers, analysis titles) generated from Python. */
let BOOTSTRAP;
async function bootstrap() {
  BOOTSTRAP ??= JSON.parse(await readFile(new URL('./bootstrap.json', import.meta.url), 'utf8'));
  return BOOTSTRAP;
}

// ---------------------------------------------------------------------------
// SigV4
// ---------------------------------------------------------------------------

const sha256hex = (value) => crypto.createHash('sha256').update(value, 'utf8').digest('hex');
const hmac = (key, value) => crypto.createHmac('sha256', key).update(value, 'utf8').digest();

/**
 * Sign one POST to the AgentCore data plane.
 *
 * The URI path is `/runtimes/{agentRuntimeArn}/invocations` (botocore's
 * bedrock-agentcore/2024-02-28 model). The ARN is a path SEGMENT, so its colons and
 * slashes must be percent-encoded, and SigV4 requires the canonical request to use
 * that same already-encoded form -- encode once, then sign and fetch the identical
 * string, or the signature will not match what the service canonicalises.
 */
function signedRequest(payload, sessionId) {
  const encodedArn = encodeURIComponent(RUNTIME_ARN);
  const path = `/runtimes/${encodedArn}/invocations`;
  const query = `qualifier=${encodeURIComponent(QUALIFIER)}`;
  const host = `${SERVICE}.${REGION}.amazonaws.com`;

  const now = new Date();
  const amzDate = now.toISOString().replace(/[:-]|\.\d{3}/g, '');
  const dateStamp = amzDate.slice(0, 8);
  const payloadHash = sha256hex(payload);

  // Signed headers must be lowercase and sorted. Content-Type is included because
  // the service reads it, and an unsigned header the service uses is a mismatch
  // waiting to happen.
  const headers = {
    'content-type': 'application/json',
    host,
    'x-amz-content-sha256': payloadHash,
    'x-amz-date': amzDate,
    'x-amzn-bedrock-agentcore-runtime-session-id': sessionId,
  };
  if (process.env.AWS_SESSION_TOKEN) {
    headers['x-amz-security-token'] = process.env.AWS_SESSION_TOKEN;
  }

  const signedHeaders = Object.keys(headers).sort().join(';');
  const canonicalHeaders = Object.keys(headers)
    .sort()
    .map((k) => `${k}:${headers[k].toString().trim()}\n`)
    .join('');

  const canonicalRequest = [
    'POST',
    path,
    query,
    canonicalHeaders,
    signedHeaders,
    payloadHash,
  ].join('\n');

  const scope = `${dateStamp}/${REGION}/${SERVICE}/aws4_request`;
  const stringToSign = [
    'AWS4-HMAC-SHA256',
    amzDate,
    scope,
    sha256hex(canonicalRequest),
  ].join('\n');

  const kDate = hmac(`AWS4${process.env.AWS_SECRET_ACCESS_KEY}`, dateStamp);
  const kRegion = hmac(kDate, REGION);
  const kService = hmac(kRegion, SERVICE);
  const kSigning = hmac(kService, 'aws4_request');
  const signature = crypto.createHmac('sha256', kSigning).update(stringToSign, 'utf8').digest('hex');

  headers.authorization =
    `AWS4-HMAC-SHA256 Credential=${process.env.AWS_ACCESS_KEY_ID}/${scope}, ` +
    `SignedHeaders=${signedHeaders}, Signature=${signature}`;

  return { url: `https://${host}${path}?${query}`, headers };
}

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

/**
 * Translate one runtime frame into zero or more UI frames.
 *
 * `done` fans out to TWO frames because the runtime combines what the UI keeps
 * apart: it carries the adjudication (the UI's `synthesis_completed`) and the run's
 * timing and cost roll-up (the UI's `run_completed`) in a single terminal object.
 */
function translate(frame, boot, state) {
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
            'AgentCore Runtime (bedrock-agentcore.invoke_agent_runtime) -> ' +
            'agents.fanout.fan_out_streaming + agents.synthesize.synthesize',
          synthesizer_model: boot.synthesizer_model,
          agents: (frame.domains ?? []).map((domain) => {
            const spec = boot.agents.find((a) => a.domain === domain) ?? {};
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

    case 'agent_done': {
      const spec = boot.agents.find((a) => a.domain === frame.domain) ?? {};
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
          rate: spec.rate ?? null,
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
    }

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
          rate: boot.synthesizer_rate ?? null,
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
      // AFTER HTTP 200. Pass it through under the name the UI's reducer checks
      // first, so a failed run shows as failed rather than as a run that stopped.
      state.sawError = true;
      return [{ event: 'error', error: frame.error, error_type: nullable(frame.error_type) }];

    default:
      // Francis's token deltas and anything added later. Forwarded verbatim rather
      // than dropped: the UI reducer ignores what it does not know, and silently
      // swallowing frames here would hide a future contract change.
      return [frame];
  }
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------

const APP_ID = /^APP-\d{3,6}$/i;

export const handler = awslambda.streamifyResponse(async (event, responseStream) => {
  const path = event.rawPath ?? '';
  const match = path.match(/\/api\/stream\/([^/?]+)/);

  const write = (obj) => responseStream.write(`data: ${JSON.stringify(obj)}\n\n`);

  const meta = {
    statusCode: 200,
    headers: {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-cache, no-store',
      'x-accel-buffering': 'no',
    },
  };
  responseStream = awslambda.HttpResponseStream.from(responseStream, meta);

  try {
    if (!match) {
      write({ event: 'error', error: `no application id in path ${JSON.stringify(path)}` });
      return;
    }
    const appId = decodeURIComponent(match[1]).toUpperCase();
    if (!APP_ID.test(appId)) {
      // Validated before signing anything: the id goes into a request body, and an
      // id-shaped allowlist is cheaper than reasoning about what the runtime does
      // with an arbitrary string.
      write({ event: 'error', error: `not a valid application id: ${appId}` });
      return;
    }

    const boot = await bootstrap();
    // AgentCore enforces a 33-character minimum on runtimeSessionId; randomUUID is
    // 36 with its hyphens, uuid4().hex would be 32 and rejected.
    const sessionId = crypto.randomUUID();
    const payload = JSON.stringify({ application_id: appId });
    const { url, headers } = signedRequest(payload, sessionId);

    const upstream = await fetch(url, { method: 'POST', headers, body: payload });
    if (!upstream.ok) {
      const detail = await upstream.text();
      write({
        event: 'error',
        error: `AgentCore returned HTTP ${upstream.status}: ${detail.slice(0, 600)}`,
      });
      return;
    }

    const state = { sawError: false };
    const decoder = new TextDecoder();
    let buffer = '';

    for await (const chunk of upstream.body) {
      buffer += decoder.decode(chunk, { stream: true });
      // SSE frames are separated by a blank line. Hold the tail: a frame split
      // across two TCP chunks must not be parsed as two broken frames.
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
        for (const out of translate(frame, boot, state)) write(out);
      }
    }
  } catch (error) {
    write({ event: 'error', error: `${error.name}: ${error.message}` });
  } finally {
    responseStream.end();
  }
});
