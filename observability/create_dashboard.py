#!/usr/bin/env python3
"""
Create/update the CloudWatch dashboard for the fraud demo.

Reads cloudwatch_dashboard.json, rewrites the region into each widget, and calls
PutDashboard. Requires boto3 + AWS credentials with cloudwatch:PutDashboard.

Usage:
  python observability/create_dashboard.py --region us-east-1 --name AgentCoreFraudDemo

Then populate metrics by running the pipeline with emission enabled:
  export EMIT_CLOUDWATCH_METRICS=1 AWS_REGION=us-east-1
  python app.py --review APP-1004     # or drive it from the UI / AgentCore Runtime
"""

import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the fraud-demo CloudWatch dashboard.")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--name", default=os.environ.get("METRICS_NAMESPACE", "AgentCoreFraudDemo"))
    parser.add_argument("--file", default=os.path.join(os.path.dirname(__file__),
                                                       "cloudwatch_dashboard.json"))
    args = parser.parse_args()

    with open(args.file) as f:
        body = json.load(f)

    # point every widget at the chosen region
    for w in body.get("widgets", []):
        if "properties" in w and "region" in w["properties"]:
            w["properties"]["region"] = args.region

    try:
        import boto3
    except ImportError:
        print("boto3 is required: pip install boto3", file=sys.stderr)
        return 1

    cw = boto3.client("cloudwatch", region_name=args.region)
    resp = cw.put_dashboard(DashboardName=args.name, DashboardBody=json.dumps(body))
    warnings = resp.get("DashboardValidationMessages", [])
    if warnings:
        print("Dashboard created with validation messages:")
        for m in warnings:
            print(f"  - {m}")
    else:
        print(f"Dashboard '{args.name}' created/updated in {args.region}.")
    print(f"Open: https://{args.region}.console.aws.amazon.com/cloudwatch/home"
          f"?region={args.region}#dashboards:name={args.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
