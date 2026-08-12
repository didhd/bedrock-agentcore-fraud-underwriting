#!/usr/bin/env node
/**
 * CDK entry point for the network and the database.
 *
 * WHY THIS IS A SEPARATE APP FROM bin/sites.ts
 *
 * Ordering. `bin/sites.ts` requires a `runtimeArn` and three built directories, none of which
 * exist before the agents are deployed. This stack has to run BEFORE them, because its outputs
 * are what put `networkMode: VPC` into `agentcore.json` for the very first `agentcore deploy`.
 *
 * That order is not cosmetic. Deploying the runtimes as PUBLIC and flipping them to VPC
 * afterwards means a second deploy of ten runtimes for no reason, and `agentcore deploy` maps
 * resources by CloudFormation logical id -- so config churn on a deployed runtime is a risk
 * that simply does not exist if the first deploy is already correct. The customer has not
 * deployed yet, so the first deploy is the only one that needs to happen.
 *
 *   1. cdk deploy PpFraudData      (this)         -> VPC, endpoints, Aurora
 *   2. deploy/one-shot.sh writes agentcore.json   -> networkMode VPC + networkConfig
 *   3. agentcore deploy                           -> ten runtimes, in-VPC from birth
 *   4. cdk deploy PpFraudSites                    -> CloudFront + the VPC-attached proxy
 */

import { App } from 'aws-cdk-lib';
import { DataStack } from '../lib/data-stack';

const app = new App();

/** A context list that may arrive comma-joined from a shell, or as a real JSON array. */
function list(key: string): string[] | undefined {
  const raw = app.node.tryGetContext(key);
  if (!raw) return undefined;
  if (Array.isArray(raw)) return raw.map(String).filter(Boolean);
  return String(raw)
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
}

function required(key: string): string {
  const value = app.node.tryGetContext(key);
  if (!value) {
    throw new Error(
      `missing CDK context '${key}'. Pass -c ${key}=... ` +
        'or let deploy/one-shot.sh supply it from your caller identity.'
    );
  }
  return String(value);
}

new DataStack(app, app.node.tryGetContext('dataStackName') ?? 'PpFraudData', {
  env: {
    account: required('account'),
    region: required('region'),
  },
  vpcId: app.node.tryGetContext('vpcId'),
  subnetIds: list('subnetIds'),
  dbSecurityGroupId: app.node.tryGetContext('dbSecurityGroupId'),
  dbClusterIdentifier: app.node.tryGetContext('dbClusterIdentifier'),
  dbSecretArn: app.node.tryGetContext('dbSecretArn'),
  databaseName: app.node.tryGetContext('databaseName'),
  auroraVersion: app.node.tryGetContext('auroraVersion'),
  // A string from `-c withNat=true`, not a boolean. `Boolean("false")` is true, which would
  // silently create a NAT Gateway for anyone who typed the value out.
  withNat: String(app.node.tryGetContext('withNat') ?? '').toLowerCase() === 'true',
  description:
    'Network and database for the Point Predictive fraud agents. Private-only by default: ' +
    'no NAT, interface endpoints for every AWS API the agents call.',
});

app.synth();
