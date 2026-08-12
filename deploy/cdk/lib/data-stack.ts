/**
 * The network and the database, in one stack, because they are one decision.
 *
 * WHY VPC AND DATABASE TOGETHER
 *
 * A database is only reachable from a subnet, and a subnet is only useful here because a
 * database sits in it. Splitting them into two stacks would mean a cross-stack export for
 * every subnet id and a deploy order to remember, in exchange for nothing: nobody wants one
 * without the other. So one stack, and it prints the mode it chose.
 *
 * THREE MODES, INFERRED FROM CONTEXT, NEVER GUESSED
 *
 *   create   no vpcId given          -> new VPC (isolated subnets, 2 AZs) + new Aurora
 *   attach   vpcId + subnetIds       -> new Aurora inside their VPC
 *   wire     vpcId + subnetIds + dbSecurityGroupId
 *                                    -> neither; only the security-group rule and the
 *                                       outputs that let the agents reach what they already
 *                                       run
 *
 * Ambiguous context is a synth-time error naming the missing value. It is never resolved by
 * picking a default, because the difference between "create a database" and "use the one you
 * already have" is not a thing to be wrong about in someone else's account.
 *
 * WHY NO NAT GATEWAY
 *
 * Verified against `aws ec2 describe-vpc-endpoint-services` in us-west-2: every AWS service
 * this system calls has an interface endpoint --
 *   bedrock-runtime           the eight Claude specialists
 *   bedrock-mantle            the GPT-5.6 synthesizer (a separate IAM and endpoint namespace)
 *   bedrock-agentcore         InvokeAgentRuntime, which is what the CloudFront Lambda calls
 *   bedrock-agentcore-control
 *   bedrock-agentcore.gateway the MCP endpoint Francis's tools come from
 *   secretsmanager, rds-data, logs, ecr.api, ecr.dkr, sts
 * plus a free S3 gateway endpoint.
 *
 * So the subnets are PRIVATE_ISOLATED and there is no NAT. That is cheaper and it is a
 * materially better answer for a fraud-data customer: nothing in the data path has a route to
 * the internet at all. `-c withNat=true` exists for the case where an agent genuinely needs
 * to reach a third-party API, and it says so rather than being the default.
 *
 * THE TRAP THIS STACK EXISTS TO AVOID
 *
 * Attaching a Lambda to a VPC REMOVES its default internet access. The CloudFront demo's
 * stream proxy calls `bedrock-agentcore` over the public API; put it in a subnet without
 * either NAT or the `bedrock-agentcore` interface endpoint and every invocation hangs until
 * it times out. The failure looks like a broken runtime, not like a missing endpoint. Those
 * endpoints are created here for exactly that reason, and `sites-stack.ts` reads this stack's
 * outputs rather than being told to trust that someone remembered.
 */

import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  type StackProps,
} from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as rds from 'aws-cdk-lib/aws-rds';
import type { Construct } from 'constructs';

export type DataStackMode = 'create' | 'attach' | 'wire';

export interface DataStackProps extends StackProps {
  /** Their VPC. Absent means create one. */
  readonly vpcId?: string;
  /** Subnets for the agents, the Lambda and the database. Required with `vpcId`. */
  readonly subnetIds?: string[];
  /**
   * The security group in front of a database they ALREADY run. Its presence is what selects
   * `wire` mode -- it is the one piece of information that only exists if the database does.
   */
  readonly dbSecurityGroupId?: string;
  /** Their existing cluster's identifier, for the outputs. Only meaningful in `wire`. */
  readonly dbClusterIdentifier?: string;
  /** Their existing credentials secret ARN. Only meaningful in `wire`. */
  readonly dbSecretArn?: string;
  /** Database name. Defaults to `postgres`. */
  readonly databaseName?: string;
  /** Add a NAT Gateway. Off by default; see the header. */
  readonly withNat?: boolean;
  /**
   * Aurora PostgreSQL engine version, e.g. `16.13`.
   *
   * A context value and not a constant, because the available versions differ BY REGION and
   * they are withdrawn over time. Hardcoding `16.4` failed here with
   * `Cannot find version 16.4 for aurora-postgresql` after CloudFormation had already built the
   * VPC -- an eleven-minute round trip to learn one string. Confirm what a region actually
   * offers with:
   *   aws rds describe-db-engine-versions --engine aurora-postgresql --region <r> \
   *     --query 'DBEngineVersions[].EngineVersion'
   */
  readonly auroraVersion?: string;
}

export class DataStack extends Stack {
  public readonly mode: DataStackMode;
  public readonly vpc: ec2.IVpc;
  /** Attached to the AgentCore runtimes, the Gateway Lambda and the CloudFront proxy. */
  public readonly clientSecurityGroup: ec2.SecurityGroup;
  public readonly subnetIds: string[];
  /** Set only in `create` mode. A gateway endpoint needs route tables, which only the real
   *  Vpc construct exposes. */
  private readonly createdVpc?: ec2.Vpc;
  /** Where the database, the endpoints and the Lambda are placed. */
  private readonly subnetSelection: ec2.SubnetSelection;

  constructor(scope: Construct, id: string, props: DataStackProps = {}) {
    super(scope, id, props);

    this.mode = resolveMode(props);

    // ---------------------------------------------------------------------
    // Network
    // ---------------------------------------------------------------------
    if (this.mode === 'create') {
      const vpc = new ec2.Vpc(this, 'Vpc', {
        maxAzs: 2,
        // DNS is not optional: AgentCore needs hostname resolution for service discovery, and
        // an interface endpoint is USELESS without private DNS -- the SDK would keep resolving
        // the public name and the call would leave through a route that does not exist.
        enableDnsHostnames: true,
        enableDnsSupport: true,
        natGateways: props.withNat ? 1 : 0,
        subnetConfiguration: props.withNat
          ? [
              { name: 'public', subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
              {
                name: 'private',
                subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
                cidrMask: 24,
              },
            ]
          : [
              // No public subnet at all in the default shape. A public subnet would not give
              // AgentCore internet access anyway -- only a NAT route does -- so including one
              // would suggest a capability it does not provide.
              {
                name: 'isolated',
                subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
                cidrMask: 24,
              },
            ],
      });
      this.vpc = vpc;
      this.createdVpc = vpc;
      // A subnetType selection, not a list of fromSubnetId lookups: CDK resolves it against
      // the real construct, so route tables and AZs come with it.
      this.subnetSelection = {
        subnetType: props.withNat
          ? ec2.SubnetType.PRIVATE_WITH_EGRESS
          : ec2.SubnetType.PRIVATE_ISOLATED,
      };
      this.subnetIds = vpc.selectSubnets(this.subnetSelection).subnetIds;
    } else {
      // `fromLookup` would need account/region credentials at synth time and would cache the
      // answer in cdk.context.json; `fromVpcAttributes` keeps synth offline and deterministic,
      // which matters because the customer runs this once, unattended, from a script.
      // The AZ list must have EXACTLY the same length as the subnet list. `fromVpcAttributes`
      // rejects anything else with "Number of privateSubnetIds (2) must be a multiple of
      // availability zones (4)" -- us-west-2 has four AZs, and a customer handing over two
      // subnets is the normal case. The real AZ of each subnet is not looked up on purpose:
      // that needs credentials at synth time and writes cdk.context.json, and nothing here
      // ever selects by AZ. Every placement in this stack names its subnets explicitly.
      const azs = props.subnetIds!.map(
        (_, index) => this.availabilityZones[index % this.availabilityZones.length],
      );
      this.vpc = ec2.Vpc.fromVpcAttributes(this, 'Vpc', {
        vpcId: props.vpcId!,
        availabilityZones: azs,
        privateSubnetIds: props.subnetIds!,
      });
      this.subnetIds = props.subnetIds!;
      this.subnetSelection = { subnets: this.privateSubnets() };
    }

    // One security group for every AgentCore-side caller. Same group on the runtimes, the
    // Gateway Lambda and the CloudFront proxy means the database's inbound rule is written
    // once and does not have to be revisited when another caller is added.
    this.clientSecurityGroup = new ec2.SecurityGroup(this, 'ClientSg', {
      vpc: this.vpc,
      description:
        'AgentCore runtimes, the Gateway SQL Lambda and the CloudFront stream proxy. ' +
        'Egress only; the database grants this group inbound.',
      allowAllOutbound: true,
    });

    // ---------------------------------------------------------------------
    // Interface endpoints. Skipped in `wire` mode: their VPC's egress is theirs to own, and
    // creating endpoints in it could duplicate ones they already pay for.
    // ---------------------------------------------------------------------
    if (this.mode !== 'wire') {
      const endpointSg = new ec2.SecurityGroup(this, 'EndpointSg', {
        vpc: this.vpc,
        description: 'Interface VPC endpoints for the AWS APIs this system calls',
        allowAllOutbound: false,
      });
      endpointSg.addIngressRule(
        this.clientSecurityGroup,
        ec2.Port.tcp(443),
        'HTTPS from the AgentCore-side callers',
      );

      // Service names verified present in us-west-2 with
      //   aws ec2 describe-vpc-endpoint-services --query 'ServiceNames[?contains(@,`bedrock`)]'
      // `bedrock-agentcore.gateway` is a sub-service name and has no ec2.InterfaceVpcEndpointAwsService
      // constant, so it is constructed by string like the rest for consistency.
      for (const service of [
        'bedrock-runtime',
        'bedrock-mantle',
        'bedrock-agentcore',
        'bedrock-agentcore-control',
        'bedrock-agentcore.gateway',
        'secretsmanager',
        'rds-data',
        'logs',
        'sts',
        'ecr.api',
        'ecr.dkr',
      ]) {
        new ec2.InterfaceVpcEndpoint(this, `Endpoint-${service.replace(/\./g, '-')}`, {
          vpc: this.vpc,
          service: new ec2.InterfaceVpcEndpointService(
            `com.amazonaws.${this.region}.${service}`,
            443,
          ),
          subnets: this.subnetSelection,
          securityGroups: [endpointSg],
          // Without this the SDK resolves the public hostname and the request never reaches
          // the endpoint. This single flag is the difference between working and hanging.
          privateDnsEnabled: true,
          // `open` defaults to TRUE, which adds an ingress rule from the whole VPC CIDR. Two
          // reasons that is wrong here. It is broader than needed -- `endpointSg` already
          // admits exactly the client group and nothing else. And it needs `vpcCidrBlock`,
          // which `Vpc.fromVpcAttributes` does not have, so in attach mode it fails synth with
          // "'vpcCidrBlock' was not supplied when creating this VPC".
          open: false,
        });
      }

      // ECR pulls read layers from S3, so a gateway endpoint is required for image pulls to
      // work without NAT. Free, unlike the interface endpoints above.
      //
      // Only in `create` mode, and the reason is a hard constraint rather than a preference: a
      // gateway endpoint is a ROUTE TABLE entry, and `Vpc.fromVpcAttributes` with bare subnet
      // ids carries no route table ids. CDK rejects it with "route table IDs are not
      // available". In `attach` mode the routing belongs to the customer, so this prints a
      // requirement instead of pretending to satisfy one.
      if (this.createdVpc) {
        this.createdVpc.addGatewayEndpoint('S3Endpoint', {
          service: ec2.GatewayVpcEndpointAwsService.S3,
          subnets: [this.subnetSelection],
        });
      } else {
        new CfnOutput(this, 'S3GatewayEndpointRequired', {
          value:
            'Add an S3 gateway endpoint to the route tables of the supplied subnets, or give ' +
            'them a NAT route. ECR image layers are read from S3 and pulls fail without it.',
          description: 'Action required in attach mode: this stack cannot edit your route tables',
        });
      }
    }

    // ---------------------------------------------------------------------
    // Database
    // ---------------------------------------------------------------------
    const databaseName = props.databaseName ?? 'postgres';
    // Verified present in us-west-2 on 2026-08-12 via describe-db-engine-versions.
    const auroraVersion = props.auroraVersion ?? '16.13';
    let clusterEndpoint: string;
    let secretArn: string;
    let clusterArn: string;
    let dataApiEnabled: boolean;

    if (this.mode === 'wire') {
      const theirs = ec2.SecurityGroup.fromSecurityGroupId(
        this,
        'TheirDbSg',
        props.dbSecurityGroupId!,
      );
      theirs.addIngressRule(
        this.clientSecurityGroup,
        ec2.Port.tcp(5432),
        'AgentCore runtimes, Gateway Lambda and CloudFront proxy',
      );
      clusterEndpoint = 'supplied-by-the-customer';
      secretArn = props.dbSecretArn ?? 'not-supplied';
      clusterArn = props.dbClusterIdentifier
        ? `arn:aws:rds:${this.region}:${this.account}:cluster:${props.dbClusterIdentifier}`
        : 'not-supplied';
      // Cannot be asserted for a cluster this stack did not create. `deploy/connect-rds.sh
      // --probe` answers it against the live cluster instead of guessing here.
      dataApiEnabled = false;
    } else {
      const cluster = new rds.DatabaseCluster(this, 'Cluster', {
        engine: rds.DatabaseClusterEngine.auroraPostgres({
          // `of(full, major)` rather than a `VER_*` constant: the constants lag the service, so
          // a version that a region offers may have no constant in the pinned aws-cdk-lib. This
          // accepts any string the region reports.
          version: rds.AuroraPostgresEngineVersion.of(auroraVersion, auroraVersion.split('.')[0]),
        }),
        // Serverless v2 scaling to a low floor: this is a POC cluster that sits idle between
        // demos, and a provisioned instance would bill the same at 3am as under load.
        serverlessV2MinCapacity: 0.5,
        serverlessV2MaxCapacity: 4,
        writer: rds.ClusterInstance.serverlessV2('writer'),
        vpc: this.vpc,
        vpcSubnets: this.subnetSelection,
        credentials: rds.Credentials.fromGeneratedSecret('ppfraud'),
        defaultDatabaseName: databaseName,
        // `signal_layer/sources/aurora.py` reads through the RDS Data API, which is the path
        // that works from a PUBLIC runtime. Enabled even though the runtimes are going into
        // the VPC, so both transports are available and the customer is not locked into the
        // one we happened to test first.
        enableDataApi: true,
        storageEncrypted: true,
        backup: { retention: Duration.days(7) },
        // A POC cluster. The customer's real data lives in their own account and this is not
        // it; leaving a snapshot behind on teardown is a surprise cost, not a safety net.
        removalPolicy: RemovalPolicy.DESTROY,
      });
      cluster.connections.allowDefaultPortFrom(
        this.clientSecurityGroup,
        'AgentCore runtimes, Gateway Lambda and CloudFront proxy',
      );
      clusterEndpoint = cluster.clusterEndpoint.hostname;
      secretArn = cluster.secret!.secretArn;
      clusterArn = cluster.clusterArn;
      dataApiEnabled = true;
    }

    // ---------------------------------------------------------------------
    // Outputs. These are the contract with deploy/one-shot.sh, which reads them back rather
    // than asking anyone to copy an ARN by hand.
    // ---------------------------------------------------------------------
    const out = (key: string, value: string, description: string) =>
      new CfnOutput(this, key, { value, description, exportName: `${this.stackName}-${key}` });

    out('Mode', this.mode, 'Which of the three modes this deploy selected');
    out('VpcId', this.vpc.vpcId, 'VPC the agents and the proxy attach to');
    out('SubnetIds', this.subnetIds.join(','), 'agentcore.json networkConfig.subnets');
    out(
      'ClientSecurityGroupId',
      this.clientSecurityGroup.securityGroupId,
      'agentcore.json networkConfig.securityGroups',
    );
    out('DatabaseName', databaseName, 'AURORA_DATABASE');
    out('ClusterEndpoint', clusterEndpoint, 'PGHOST for the socket transport');
    out('ClusterArn', clusterArn, 'AURORA_CLUSTER_ARN for the Data API transport');
    out('SecretArn', secretArn, 'AURORA_SECRET_ARN');
    out(
      'DataApiEnabled',
      String(dataApiEnabled),
      'Whether the Data API is known-enabled. False in wire mode means unknown, not disabled -- run ./deploy/connect-rds.sh --probe',
    );
    out(
      'PrivateEgress',
      this.mode === 'wire' ? 'customer-owned' : props.withNat ? 'nat' : 'vpc-endpoints-only',
      'How the subnets reach AWS APIs',
    );
  }

  /**
   * The subnets to place things in, for whichever mode is live.
   *
   * MEMOISED, and that is not an optimisation. `Subnet.fromSubnetId` CREATES a construct, so
   * calling this twice threw `There is already a Construct with name 'PlacementSubnet0'` and
   * synth failed in all three modes. Building the list once is the fix.
   */
  private placementSubnets?: ec2.ISubnet[];

  private privateSubnets(): ec2.ISubnet[] {
    if (!this.placementSubnets) {
      this.placementSubnets = this.subnetIds.map((id, index) =>
        ec2.Subnet.fromSubnetId(this, `PlacementSubnet${index}`, id),
      );
    }
    return this.placementSubnets;
  }
}

/**
 * Pick the mode, or fail with the missing value named.
 *
 * Deliberately strict. Half-supplied context is far more likely to be a typo or a truncated
 * shell variable than a considered choice, and the cost of guessing is either an unwanted
 * database or an agent pointed at the wrong one.
 */
function resolveMode(props: DataStackProps): DataStackMode {
  const hasVpc = Boolean(props.vpcId);
  const hasSubnets = Boolean(props.subnetIds?.length);
  const hasDbSg = Boolean(props.dbSecurityGroupId);

  if (!hasVpc && !hasSubnets && !hasDbSg) return 'create';

  if (hasVpc !== hasSubnets) {
    throw new Error(
      'vpcId and subnetIds must be supplied together. ' +
        `Got vpcId=${props.vpcId ?? '(none)'} subnetIds=${props.subnetIds?.join(',') ?? '(none)'}. ` +
        'Pass both, or neither to create a new VPC.',
    );
  }
  if (hasDbSg && !hasVpc) {
    throw new Error(
      'dbSecurityGroupId was given without vpcId/subnetIds. An existing database implies an ' +
        'existing VPC; pass -c vpcId=... -c subnetIds=... as well.',
    );
  }
  return hasDbSg ? 'wire' : 'attach';
}
