/**
 * Which deployment this bundle was built for.
 *
 * There is exactly one difference between the two builds, and it is a question of
 * who holds the credentials.
 *
 * LOCAL (`npm run build`, served by ui/server.py on :8080)
 *   The browser talks to a FastAPI process running as you, with your AWS
 *   credentials. The "AgentCore Runtime" backend option is genuinely useful there:
 *   you can point the page straight at a deployed runtime's invocation URL and
 *   watch the deployed thing rather than the local pipeline. It needs a bearer
 *   token, which is fine on your own machine.
 *
 * EDGE (`npm run build -- --mode edge`, uploaded to S3 behind CloudFront)
 *   The browser talks to CloudFront, and `/api/stream/*` is already an AgentCore
 *   Runtime call -- made server-side by a Lambda, signed with SigV4 from its
 *   execution role, with the runtime ARN in `RUNTIME_ARN`. So the manual URL and
 *   bearer-token inputs are worse than redundant:
 *
 *     - There is nothing to configure. The path is already AgentCore.
 *     - They ask a viewer to paste a bearer token into a browser on a public URL,
 *       which is the opposite of what the Lambda exists to avoid.
 *     - They render as two empty boxes with no correct value, which reads as
 *       "unconfigured" on a deployment that is fully configured.
 *
 * So the option is compiled out of the edge build. `__EDGE_BUILD__` is replaced with
 * a boolean literal by Vite's `define`, so the condition folds at build time and
 * Rollup drops the branch: the edge bundle does not merely hide the inputs, it does
 * not contain them. It is a literal rather than a string compared at runtime because
 * a string comparison across a module boundary left the markup in the bundle,
 * unreachable -- harmless, but not what "removed" should mean.
 *
 * WHY A SEPARATE OUTPUT DIRECTORY, NOT A FLAG ON ONE BUILD
 * `ui/dist` is what ui/server.py serves on localhost. Building the edge variant
 * over it would silently change what you see locally, so `--mode edge` writes to
 * `ui/dist-edge` (see ui/vite.config.ts) and deploy/deploy-sites.sh uploads that.
 * `ui/dist` is never touched by a deployment.
 */

/**
 * Whether to offer the manual "AgentCore Runtime" backend, with its invocation-URL
 * and bearer-token inputs. True for local builds, false for the edge build.
 */
export const ALLOW_MANUAL_RUNTIME_BACKEND: boolean = !__EDGE_BUILD__;

/**
 * How this build reaches the runtime, for display. Only meaningful when the manual
 * backend is compiled out -- otherwise the BackendBar's own selector says it.
 */
export const EDGE_BACKEND_LABEL = 'AgentCore Runtime (server-side)';

/**
 * The one-line explanation shown next to that label. States where the ARN actually
 * lives, because "where is this configured" is the first question the panel used to
 * answer with an empty text box.
 *
 * Note the singular: this UI runs the batch underwriting path only, so the edge
 * Lambda fronts the `underwriter` runtime. The `francis` runtime is deployed by the
 * same agent stack but is not reachable from this page -- which is why one field was
 * ever enough, and another reason a URL box here was misleading.
 */
export const EDGE_BACKEND_DETAIL =
  'Requests go to CloudFront, which invokes the underwriter AgentCore Runtime ' +
  'server-side with SigV4 from the proxy Lambda’s execution role. The runtime ARN ' +
  'is the Lambda’s RUNTIME_ARN environment variable, set by CDK from the agent ' +
  'deployment — there is no browser-side credential and nothing to enter here.';

/**
 * Replaced with `true` or `false` by Vite's `define` (ui/vite.config.ts). Declared
 * here so TypeScript knows about a symbol that only exists after bundling.
 */
declare const __EDGE_BUILD__: boolean;
