import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import boto3
import requests

# ----------------------------
# Config
# ----------------------------

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "60"))

REGIONS = [
    r.strip()
    for r in os.getenv("BEDROCK_SOURCE_REGIONS", "us-east-1,us-east-2,us-west-2").split(",")
    if r.strip()
]

# IMPORTANT:
# Use the exact Bedrock ModelId values you want to count.
# For your current screenshots, these are the main ones:
#   us.anthropic.claude-opus-4-6-v1
#   us.anthropic.claude-sonnet-4-6
#
# You can override with a GitHub Actions env var:
# BEDROCK_MODEL_IDS=us.anthropic.claude-opus-4-6-v1,us.anthropic.claude-sonnet-4-6
MODEL_IDS = [
    m.strip()
    for m in os.getenv(
        "BEDROCK_MODEL_IDS",
        "us.anthropic.claude-opus-4-6-v1,us.anthropic.claude-sonnet-4-6",
    ).split(",")
    if m.strip()
]

METRIC_NAMES = [
    "InputTokenCount",
    "OutputTokenCount",
    "CacheReadInputTokens",
    "CacheWriteInputTokens",
]

GIST_ID = os.environ.get("GIST_ID")
GITHUB_TOKEN = os.environ.get("GH_PAT")

if not GIST_ID or not GITHUB_TOKEN:
    raise ValueError("Missing GIST_ID or GH_PAT environment variables.")


# ----------------------------
# Helpers
# ----------------------------

def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def build_queries(region, model_ids):
    queries = []
    meta = {}
    idx = 0

    for model_id in model_ids:
        for metric_name in METRIC_NAMES:
            qid = f"q{idx}"
            queries.append(
                {
                    "Id": qid,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/Bedrock",
                            "MetricName": metric_name,
                            "Dimensions": [
                                {"Name": "ModelId", "Value": model_id}
                            ],
                        },
                        "Period": 86400,   # daily bins
                        "Stat": "Sum",
                    },
                    "ReturnData": True,
                }
            )
            meta[qid] = {
                "region": region,
                "model_id": model_id,
                "metric_name": metric_name,
            }
            idx += 1

    return queries, meta


def fetch_metric_batch(cloudwatch, batch, start_time, end_time):
    all_results = []
    next_token = None

    while True:
        kwargs = {
            "MetricDataQueries": batch,
            "StartTime": start_time,
            "EndTime": end_time,
            "ScanBy": "TimestampAscending",
        }
        if next_token:
            kwargs["NextToken"] = next_token

        response = cloudwatch.get_metric_data(**kwargs)
        all_results.extend(response.get("MetricDataResults", []))

        next_token = response.get("NextToken")
        if not next_token:
            break

    return all_results


# ----------------------------
# Main aggregation
# ----------------------------

def get_bedrock_tokens():
    start_time = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    end_time = datetime.now(timezone.utc)

    totals_by_metric = defaultdict(int)
    totals_by_region = defaultdict(int)
    totals_by_model = defaultdict(int)
    detailed_by_model_metric = defaultdict(int)

    for region in REGIONS:
        cloudwatch = boto3.client("cloudwatch", region_name=region)
        queries, meta = build_queries(region, MODEL_IDS)

        for batch in chunked(queries, 500):
            results = fetch_metric_batch(cloudwatch, batch, start_time, end_time)

            for result in results:
                qid = result.get("Id")
                values = result.get("Values", [])
                if not qid or not values:
                    continue

                total = int(sum(values))
                info = meta[qid]

                metric_name = info["metric_name"]
                model_id = info["model_id"]

                totals_by_metric[metric_name] += total
                totals_by_region[region] += total
                totals_by_model[(region, model_id)] += total
                detailed_by_model_metric[(region, model_id, metric_name)] += total

    print("\nPer-region totals:")
    for region in REGIONS:
        print(f"{region} -> {totals_by_region.get(region, 0):,}")

    print("\nMetric breakdown:")
    ordered_breakdown = {}
    for metric_name in METRIC_NAMES:
        ordered_breakdown[metric_name] = int(totals_by_metric.get(metric_name, 0))
        print(f"{metric_name} -> {ordered_breakdown[metric_name]:,}")

    print("\nTop models:")
    for (region, model_id), total in sorted(
        totals_by_model.items(), key=lambda x: x[1], reverse=True
    ):
        print(f"{region} | {model_id} -> {total:,}")
        for metric_name in METRIC_NAMES:
            metric_total = detailed_by_model_metric.get((region, model_id, metric_name), 0)
            if metric_total:
                print(f"    {metric_name}: {metric_total:,}")

    total_tokens = int(sum(ordered_breakdown.values()))
    return total_tokens, ordered_breakdown


# ----------------------------
# Gist update
# ----------------------------

def update_gist(formatted_total):
    svg_content = f"""<svg width="430" height="120" viewBox="0 0 430 120" xmlns="http://www.w3.org/2000/svg">
  <rect width="430" height="120" rx="12" fill="#0d1117" stroke="#30363d" stroke-width="2"/>
  <text x="24" y="38" font-family="Arial, sans-serif" font-size="14" font-weight="bold" fill="#8b949e">TOKENS SACRIFICED TO LLM GODS</text>
  <defs>
    <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#6C3AED;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#3182ce;stop-opacity:1" />
    </linearGradient>
  </defs>
  <text x="24" y="85" font-family="Arial, sans-serif" font-size="36" font-weight="bold" fill="url(#grad)">{formatted_total}</text>
</svg>"""

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    payload = {"files": {"tokens.svg": {"content": svg_content}}}

    response = requests.patch(
        f"https://api.github.com/gists/{GIST_ID}",
        headers=headers,
        json=payload,
        timeout=30,
    )

    if response.status_code == 200:
        print("Success! Gist updated with the new model metrics.")
    else:
        raise RuntimeError(f"Failed to update gist: {response.status_code} {response.text}")


def main():
    print(f"LOOKBACK_DAYS = {LOOKBACK_DAYS}")
    print(f"REGIONS = {REGIONS}")
    print(f"MODEL_IDS = {MODEL_IDS}")

    total_tokens, breakdown = get_bedrock_tokens()
    formatted_total = f"{total_tokens:,}"

    print(f"\nFinal Aggregation -> Exact Total: {formatted_total}")
    print(f"Final Breakdown -> {breakdown}")

    update_gist(formatted_total)


if __name__ == "__main__":
    main()
