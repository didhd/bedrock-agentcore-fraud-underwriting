#!/usr/bin/env node
/**
 * CDK entry point for the two CloudFront sites.
 *
 * Everything variable is a CDK context value, not a hard-coded constant, so the
 * customer deploys into their own account by editing `cdk.json` (or passing `-c`)
 * and never by editing TypeScript. `deploy/deploy-sites.sh` fills the two values
 * that cannot be known ahead of time -- the account and the runtime ARN -- by
 * reading them back from the AgentCore deployment rather than asking anyone to
 * copy an ARN by hand.
 */

import { App } from 'aws-cdk-lib';
import { SitesStack } from '../lib/sites-stack';
import * as path from 'node:path';
import { existsSync } from 'node:fs';

const app = new App();

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');

/** A required context value, with an error that says how to supply it. */
function required(key: string): string {
  const value = app.node.tryGetContext(key);
  if (!value) {
    throw new Error(
      `missing CDK context '${key}'. Pass -c ${key}=... or set it in ` +
        `deploy/cdk/cdk.json. deploy/deploy-sites.sh sets it for you.`
    );
  }
  return String(value);
}

/** A build directory that must exist, with the command that produces it. */
function builtDir(relative: string, howToBuild: string): string {
  const dir = path.join(REPO_ROOT, relative);
  if (!existsSync(dir)) {
    throw new Error(`${relative} does not exist. Build it first:\n  ${howToBuild}`);
  }
  return dir;
}

/** A context list that may arrive comma-joined from a shell, or as a real JSON array. */
function listContext(key: string): string[] | undefined {
  const raw = app.node.tryGetContext(key);
  if (!raw) return undefined;
  if (Array.isArray(raw)) return raw.map(String).filter(Boolean);
  return String(raw)
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
}

const runtimeArn = required('runtimeArn');

/**
 * Every seat's runtime ARN, as `CHAT_ARN_<NAME>` environment variables for the proxy
 * Lambda. Passed as one JSON context value by deploy/deploy-sites.sh, which reads them
 * from the AgentCore deployment -- the same reason the orchestrator's ARN is not typed by
 * hand. Absent seats simply do not appear, and the chat tab reports them unavailable.
 */
const chatRuntimeArns: Record<string, string> = (() => {
  const raw = app.node.tryGetContext('chatRuntimeArns');
  if (!raw) return {};
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    return Object.fromEntries(
      Object.entries(parsed as Record<string, string>).map(([name, arn]) => [
        `CHAT_ARN_${name.toUpperCase()}`,
        arn,
      ])
    );
  } catch {
    throw new Error(`context 'chatRuntimeArns' is not valid JSON: ${String(raw).slice(0, 120)}`);
  }
})();
const runtimeRegion = app.node.tryGetContext('runtimeRegion') ?? runtimeArn.split(':')[3];

new SitesStack(app, app.node.tryGetContext('stackName') ?? 'PpFraudSites', {
  env: {
    account: required('account'),
    // The sites can live anywhere; they are fronted by CloudFront either way. This
    // defaults to the runtime's region only so a customer who sets nothing gets one
    // region for everything.
    region: app.node.tryGetContext('sitesRegion') ?? runtimeRegion,
  },
  runtimeArn,
  runtimeRegion,
  runtimeQualifier: app.node.tryGetContext('runtimeQualifier') ?? 'DEFAULT',
  docsDir: builtDir('site/out', 'cd site && npm install && npm run build'),
  // dist-edge, not dist: the deployed bundle is the one with the manual runtime
  // backend compiled out. ui/dist stays the localhost artefact.
  demoDir: builtDir('ui/dist-edge', 'cd ui && npm install && npm run build -- --mode edge'),
  apiDir: builtDir(
    'deploy/cdk/.build/api',
    'python3 deploy/cdk/scripts/build-static-api.py --out deploy/cdk/.build'
  ),
  bootstrapFile: path.join(REPO_ROOT, 'deploy/cdk/.build/bootstrap.json'),
  chatRuntimeArns,
  // Supplied by deploy/one-shot.sh from the PpFraudData stack outputs. Absent means the proxy
  // stays outside the VPC, which is correct while the database is still public or unused.
  vpcId: app.node.tryGetContext('vpcId'),
  subnetIds: listContext('subnetIds'),
  securityGroupIds: listContext('securityGroupIds'),
  description:
    'Two CloudFront endpoints for the Point Predictive engagement: the docs site ' +
    'and the live fraud-underwriting demo. The AgentCore runtimes are NOT owned ' +
    'here; they belong to `agentcore deploy`.',
});

app.synth();
