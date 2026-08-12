/**
 * The two customer-facing CloudFront endpoints.
 *
 *   docs  ->  the Fumadocs engagement record, 40 pages, prerendered.  Pure S3.
 *   demo  ->  the fraud-underwriting UI.  S3 for everything except one path.
 *
 * WHY TWO DISTRIBUTIONS AND NOT ONE
 *
 * They have different lifecycles and different audiences. The docs site is
 * regenerated whenever prose changes and is safe to hand to anyone; the demo runs
 * live Bedrock calls and costs money per click. Separate distributions mean the
 * demo can be locked down, throttled or torn down without touching the reading
 * material, and either can be invalidated without busting the other's cache.
 *
 * WHY THE DEMO NEEDS ANY COMPUTE
 *
 * Only `/api/stream/*` does. The other three endpoints the UI calls are constant
 * for a given build, so `scripts/build-static-api.py` bakes them into S3 objects
 * at the exact extensionless paths the UI fetches. A live run cannot be baked, and
 * CloudFront cannot reach the runtime by itself -- OAC signs SigV4 for S3, Lambda
 * function URLs and VPC origins, not for `bedrock-agentcore`, and a browser holds
 * no credentials to sign with. Hence exactly one Lambda, on exactly one path.
 *
 * WHAT THIS STACK DOES NOT OWN
 *
 * The AgentCore runtimes. Those belong to `agentcore deploy` and its own stack
 * (`AgentCore-ppFraud-default`); this stack only reads the runtime ARN and is
 * given permission to invoke it. Keeping them apart is what lets the customer
 * redeploy an agent without re-uploading a website, and it avoids a second copy of
 * any S3 bucket -- the CDK bootstrap staging bucket is shared, and each site owns
 * exactly one bucket of its own.
 */

import {
  CfnOutput,
  Duration,
  RemovalPolicy,
  Stack,
  type StackProps,
} from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as nodejs from 'aws-cdk-lib/aws-lambda-nodejs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { Construct } from 'constructs';
import * as path from 'node:path';

export interface SitesStackProps extends StackProps {
  /** ARN of the deployed `underwriter` AgentCore runtime. */
  readonly runtimeArn: string;
  /** Region the runtime lives in (not necessarily this stack's region). */
  readonly runtimeRegion: string;
  /** Runtime endpoint qualifier. `DEFAULT` unless a named endpoint was added. */
  readonly runtimeQualifier?: string;
  /** Directory holding the Next.js static export (`site/out`). */
  readonly docsDir: string;
  /** Directory holding the Vite build (`ui/dist`). */
  readonly demoDir: string;
  /** Directory holding the baked API objects (`deploy/cdk/.build/api`). */
  readonly apiDir: string;
  /** File holding the generated per-model facts (`deploy/cdk/.build/bootstrap.json`). */
  readonly bootstrapFile: string;
  /**
   * `{ CHAT_ARN_FRANCIS: arn, CHAT_ARN_IDENTITY: arn, ... }` for the chat tab. Empty when
   * only the orchestrator is deployed, in which case the chat seats report themselves
   * unavailable rather than answering from somewhere else.
   */
  readonly chatRuntimeArns?: Record<string, string>;
}

export class SitesStack extends Stack {
  constructor(scope: Construct, id: string, props: SitesStackProps) {
    super(scope, id, props);

    // -----------------------------------------------------------------------
    // Shared: a viewer-request function that maps clean URLs onto S3 keys.
    //
    // A Next.js static export writes `out/docs/architecture.html`, but the link in
    // the sidebar is `/docs/architecture`. S3 has no notion of "try adding .html",
    // so without this every internal link 404s -- the site's own navigation would
    // be broken while the home page loaded fine.
    //
    // A CloudFront Function rather than Lambda@Edge: this is string manipulation
    // on a URI, it runs in about a millisecond at the edge, and it has no cold
    // start. Lambda@Edge would add both latency and a us-east-1 deployment
    // dependency for no benefit.
    // -----------------------------------------------------------------------
    const cleanUrls = new cloudfront.Function(this, 'CleanUrls', {
      comment: 'Map extensionless paths to the .html files a Next static export writes',
      code: cloudfront.FunctionCode.fromInline(`
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  // Data routes are files, not pages. Next's static export writes the Fumadocs
  // search index to out/api/search -- 9.7 MB, no extension -- and the client fetches
  // exactly '/api/search'. Appending .html to it turned the site's search box into a
  // silent 404 while every page still loaded, so this exclusion is the whole reason
  // the prefix is checked before anything else.
  if (uri.indexOf('/api/') === 0) {
    return request;
  }
  if (uri.endsWith('/')) {
    request.uri = uri + 'index.html';
  } else {
    // Only append .html when the final segment has no extension of its own, so
    // /_next/static/chunks/main-abc123.js and /og/docs/x/image.png pass through.
    var last = uri.split('/').pop();
    if (last && last.indexOf('.') === -1) {
      request.uri = uri + '.html';
    }
  }
  return request;
}
      `),
    });

    // =======================================================================
    // 1. DOCS  --  static only
    // =======================================================================
    const docsBucket = new s3.Bucket(this, 'DocsBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      // The bucket holds only build output. Nothing here is a source of truth, so
      // destroying it with the stack loses nothing that `npm run build` cannot
      // regenerate -- and leaving orphaned buckets behind is how accounts fill up.
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const docsDistribution = new cloudfront.Distribution(this, 'DocsDistribution', {
      comment: 'Point Predictive engagement record (Fumadocs static export)',
      defaultRootObject: 'index.html',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(docsBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        functionAssociations: [
          { function: cleanUrls, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST },
        ],
      },
      // Next's export writes a real 404.html; serving it with the 404 status keeps
      // the status code honest instead of dressing a miss up as a 200.
      errorResponses: [
        { httpStatus: 403, responseHttpStatus: 404, responsePagePath: '/404.html' },
        { httpStatus: 404, responseHttpStatus: 404, responsePagePath: '/404.html' },
      ],
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
    });

    new s3deploy.BucketDeployment(this, 'DocsDeployment', {
      sources: [s3deploy.Source.asset(props.docsDir)],
      destinationBucket: docsBucket,
      // 1 GB, not the 128 MB default, and this is not a guess. On the default the
      // docs deployment TIMED OUT: `Duration: 900000 ms ... Max Memory Used: 126 MB
      // ... Status: timeout`, having uploaded 517 of 580 files. Lambda scales CPU and
      // network with memory, so a memory-starved uploader's throughput collapsed to
      // ~250 KiB/s and 37 MB could not finish inside the 15-minute cap. CloudFormation
      // then waits on a custom resource that will never answer, so the whole stack
      // hangs rather than failing fast -- which is what makes this worth pinning
      // rather than leaving to the default.
      memoryLimit: 1024,
      distribution: docsDistribution,
      distributionPaths: ['/*'],
      prune: true,
    });

    // =======================================================================
    // 2. DEMO  --  static shell + baked API + one streaming Lambda
    // =======================================================================
    const demoBucket = new s3.Bucket(this, 'DemoBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // NodejsFunction, so esbuild bundles @aws-sdk/client-bedrock-agentcore into the
    // deployment package. That SDK is NOT part of the Node 22 runtime's built-in AWS
    // SDK set, and the alternative -- signing the request by hand to avoid a bundler
    // -- is what produced a 403 the first time round: SigV4 requires every path
    // segment to be URI-encoded TWICE for non-S3 services, and the runtime ARN is a
    // path segment. See the handler's header comment. Bundling the SDK removes that
    // whole class of bug instead of patching one instance of it.
    const streamProxy = new nodejs.NodejsFunction(this, 'StreamProxy', {
      description:
        'Streams one AgentCore underwriting run to the browser, translating the ' +
        "runtime's SSE vocabulary into the one ui/src/hooks/useRun.ts consumes",
      runtime: lambda.Runtime.NODEJS_22_X,
      entry: path.join(__dirname, '..', 'lambda', 'stream-proxy.mjs'),
      handler: 'handler',
      bundling: {
        format: nodejs.OutputFormat.CJS,
        target: 'node22',
        // `awslambda` is a global the Lambda runtime injects, not a module. Naming it
        // external stops esbuild trying to resolve it and failing the build.
        externalModules: [],
        banner:
          '// bundled by esbuild via aws-cdk-lib/aws-lambda-nodejs; see deploy/cdk/lib/sites-stack.ts',
        // bootstrap.json is generated by deploy/deploy-sites.sh; esbuild inlines it,
        // so there is no runtime file read and no asset to keep in sync.
        loader: { '.json': 'json' },
        minify: false, // a readable stack trace matters more here than 40 KB
      },
      // A full run measured 41.86s end to end against the deployed runtime, and the
      // slowest single specialist took 29.8s. 5 minutes leaves headroom for a
      // degraded run without this timeout ever being the thing that decides the
      // outcome.
      timeout: Duration.minutes(5),
      // The work is one API call and then piping bytes; it is entirely IO-bound.
      // More memory buys nothing but a bigger bill.
      memorySize: 512,
      environment: {
        RUNTIME_ARN: props.runtimeArn,
        RUNTIME_REGION: props.runtimeRegion,
        RUNTIME_QUALIFIER: props.runtimeQualifier ?? 'DEFAULT',
        // Chat needs every seat, not just the orchestrator's batch path. Written below
        // once the sibling runtimes are known.
        ...(props.chatRuntimeArns ?? {}),
      },
    });

    // Credentials come from this role through the SDK's default provider chain --
    // no keys, nothing to rotate. Scoped to the one runtime this stack fronts rather
    // than `runtime/*`: the account holds 39 other runtimes and none of them should
    // be reachable from a public URL.
    // Every runtime this proxy is allowed to reach: the batch orchestrator plus each
    // chat seat. Enumerated rather than wildcarded, because the account holds 39 other
    // runtimes and none of them should be reachable from a public URL.
    const reachable = [props.runtimeArn, ...Object.values(props.chatRuntimeArns ?? {})];
    streamProxy.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:InvokeAgentRuntime'],
        resources: [...new Set(reachable.flatMap((arn) => [arn, `${arn}/*`]))],
      })
    );

    // RESPONSE_STREAM is the whole reason this is a function URL and not an API
    // Gateway route. The UI's value is watching eight agents land one by one over
    // ~30 seconds; a buffered response would deliver all of it at once after 42
    // seconds of blank screen, and API Gateway's hard 29s integration timeout would
    // cut the run off before it finished either way.
    const streamUrl = streamProxy.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.AWS_IAM,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
    });

    const demoDistribution = new cloudfront.Distribution(this, 'DemoDistribution', {
      comment: 'Point Predictive fraud underwriting demo (live Bedrock)',
      defaultRootObject: 'index.html',
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(demoBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      additionalBehaviors: {
        // The baked endpoints. Served from S3, but never cached: /api/health
        // reports which backend is wired and /api/applications lists the fixtures,
        // and a stale copy of either after a redeploy is a confusing demo.
        '/api/health': {
          origin: origins.S3BucketOrigin.withOriginAccessControl(demoBucket),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        },
        '/api/applications': {
          origin: origins.S3BucketOrigin.withOriginAccessControl(demoBucket),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        },
        // The nine seats and their configuration. Baked WITHOUT prompt text -- this is a
        // public, unauthenticated URL and the prompts are customer-confidential; see
        // build-static-api.py.
        '/api/agents': {
          origin: origins.S3BucketOrigin.withOriginAccessControl(demoBucket),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        },
        '/api/payload/*': {
          origin: origins.S3BucketOrigin.withOriginAccessControl(demoBucket),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        },
        // Chat with one agent. Same Lambda, same reason it exists: a browser cannot
        // sign SigV4 to bedrock-agentcore, and CloudFront's OAC cannot sign for it either.
        '/api/chat/*': {
          origin: origins.FunctionUrlOrigin.withOriginAccessControl(streamUrl, {
            readTimeout: Duration.seconds(60),
            keepaliveTimeout: Duration.seconds(60),
          }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
        // The live run. OAC signs SigV4 to the function URL so the URL itself stays
        // IAM-authenticated and is not independently callable.
        '/api/stream/*': {
          origin: origins.FunctionUrlOrigin.withOriginAccessControl(streamUrl, {
            // 42s measured end to end; the default 30s would abort a healthy run.
            readTimeout: Duration.seconds(60),
            keepaliveTimeout: Duration.seconds(60),
          }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          // Caching an SSE stream would replay one application's adjudication to
          // every later viewer, tokens, cost and all.
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          // No ResponseHeadersPolicy here on purpose. The obvious move is to inject
          // `X-Accel-Buffering: no` and a no-store Cache-Control at the edge, and
          // CloudFront rejects it outright:
          //
          //   AWS::CloudFront::ResponseHeadersPolicy: The parameter CustomHeaders
          //   contains X-Accel-Buffering that is not allowed.
          //
          // X-Accel-Buffering is on CloudFront's denylist for custom headers. It is
          // also unnecessary: the Lambda already sets both headers on its own
          // response via HttpResponseStream.from, which is the correct place for them
          // -- they describe that response, not this behaviour -- and
          // CACHING_DISABLED above is what actually stops CloudFront caching the
          // stream.
        },
      },
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
    });

    new s3deploy.BucketDeployment(this, 'DemoAppDeployment', {
      sources: [s3deploy.Source.asset(props.demoDir)],
      destinationBucket: demoBucket,
      // 1 GB, not the 128 MB default, and this is not a guess. On the default the
      // docs deployment TIMED OUT: `Duration: 900000 ms ... Max Memory Used: 126 MB
      // ... Status: timeout`, having uploaded 517 of 580 files. Lambda scales CPU and
      // network with memory, so a memory-starved uploader's throughput collapsed to
      // ~250 KiB/s and 37 MB could not finish inside the 15-minute cap. CloudFormation
      // then waits on a custom resource that will never answer, so the whole stack
      // hangs rather than failing fast -- which is what makes this worth pinning
      // rather than leaving to the default.
      memoryLimit: 1024,
      distribution: demoDistribution,
      distributionPaths: ['/*'],
      prune: false,
    });

    // Separate deployment purely so Content-Type can be forced. The baked API
    // objects have no `.json` suffix -- they are named for the URLs the UI fetches
    // -- so S3 would otherwise label them application/octet-stream and the browser
    // would refuse to parse them.
    new s3deploy.BucketDeployment(this, 'DemoApiDeployment', {
      sources: [s3deploy.Source.asset(props.apiDir)],
      destinationBucket: demoBucket,
      destinationKeyPrefix: 'api',
      contentType: 'application/json',
      // 1 GB, not the 128 MB default, and this is not a guess. On the default the
      // docs deployment TIMED OUT: `Duration: 900000 ms ... Max Memory Used: 126 MB
      // ... Status: timeout`, having uploaded 517 of 580 files. Lambda scales CPU and
      // network with memory, so a memory-starved uploader's throughput collapsed to
      // ~250 KiB/s and 37 MB could not finish inside the 15-minute cap. CloudFormation
      // then waits on a custom resource that will never answer, so the whole stack
      // hangs rather than failing fast -- which is what makes this worth pinning
      // rather than leaving to the default.
      memoryLimit: 1024,
      distribution: demoDistribution,
      distributionPaths: ['/api/*'],
      prune: false,
    });

    // -----------------------------------------------------------------------
    new CfnOutput(this, 'DocsUrl', {
      description: 'CloudFront endpoint 1 of 2 - engagement record',
      value: `https://${docsDistribution.distributionDomainName}`,
    });
    new CfnOutput(this, 'DemoUrl', {
      description: 'CloudFront endpoint 2 of 2 - live fraud underwriting demo',
      value: `https://${demoDistribution.distributionDomainName}`,
    });
    new CfnOutput(this, 'DocsBucketName', { value: docsBucket.bucketName });
    new CfnOutput(this, 'DemoBucketName', { value: demoBucket.bucketName });
    new CfnOutput(this, 'StreamProxyName', { value: streamProxy.functionName });
    new CfnOutput(this, 'FrontedRuntimeArn', { value: props.runtimeArn });
  }
}
