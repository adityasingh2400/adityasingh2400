import os
import requests
import boto3
from datetime import datetime, timedelta, timezone
from collections import defaultdict

LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "60"))

REGIONS = [
    r.strip()
    for r in os.getenv("BEDROCK_SOURCE_REGIONS", "us-east-1,us-east-2,us-west-2").split(",")
    if r.strip()
]

# Optional manual overrides.
# Strongly recommended to set this so you do not depend only on recent auto-discovery.
# Example:
# BEDROCK_MODEL_IDS=anthropic.claude-opus-4-6-20260205-v1:0,anthropic.claude-sonnet-4-6-20260219-v1:0,anthropic.claude-opus-4-5-20251124-v1:0
MANUAL_MODEL_IDS = {
    m.strip()
    for m in os.getenv("BEDROCK_MODEL_IDS", "").split(",")
    if m.strip()
}

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

def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]

def discover_recent_model_ids(region: str) -> set[str]:
    """
    Auto-discovers Bedrock ModelId dimension values that have reported recently.
    This is only a helper. Older inactive model IDs may not show up, so BEDROCK_MODEL_IDS
    should still be set for best accuracy.
    """
    cw = boto3.client("cloudwatch", region_name=region)
    discovered = set()
    next_token = None

    while True:
        kwargs = {
            "Namespace": "AWS/Bedrock",
            "MetricName": "InputTokenCount",
        }
        if next_token:
            kwargs["NextToken"] = next_token

        response = cw.list_metrics(**kwargs)

        for metric in response.get("Metrics", []):
            for dim in metric.get("Dimensions", []):
                if dim.get("Name") == "ModelId" and dim.get("Value"):
                    discovered.add(dim["Value"])

        next_token = response.get("NextToken")
        if not next_token:
            break

    return discovered

def get_all_model_ids() -> dict[str, set[str]]:
    region_to_models = {}
    for region in REGIONS:
        try:
            discovered = discover_recent_model_ids(region)
            all_ids = set(discovered) | set(MANUAL_MODEL_IDS)
            region_to_models[region] = all_ids
            print(f"{region} discovered model ids: {sorted(discovered)}")
            if MANUAL_MODEL_IDS:
                print(f"{region} manual model ids merged in: {sorted(MANUAL_MODEL_IDS)}")
        except Exception as e:
            print(f"Error discovering model IDs in {region}: {e}")
            region_to_models[region] = set(MANUAL_MODEL_IDS)

    return region_to_models

def build_queries(region: str, model_ids: set[str]):
    queries = []
    meta = {}
    idx = 0

    for model_id in sorted(model_ids):
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
                        "Period": 86400,
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

def fetch_metric_batch(cw, batch, start_time, end_time):
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

        response = cw.get_metric_data(**kwargs)
        all_results.extend(response.get("MetricDataResults", []))

        next_token = response.get("NextToken")
        if not next_token:
            break

    return all_results

def get_bedrock_tokens():
    start_time = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    end_time = datetime.now(timezone.utc)

    region_to_models = get_all_model_ids()

    totals_by_metric = defaultdict(int)
    totals_by_region = defaultdict(int)
    totals_by_model = defaultdict(int)

    for region, model_ids in region_to_models.items():
        if not model_ids:
            print(f"{region} -> no model ids found")
            continue

        cw = boto3.client("cloudwatch", region_name=region)
        queries, meta = build_queries(region, model_ids)

        for batch in chunked(queries, 500):
            results = fetch_metric_batch(cw, batch, start_time, end_time)

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

    print("\nPer-region totals:")
    for region in REGIONS:
        print(f"{region} -> {totals_by_region.get(region, 0):,}")

    print("\nMetric breakdown:")
    for metric_name in METRIC_NAMES:
        print(f"{metric_name} -> {totals_by_metric.get(metric_name, 0):,}")

    print("\nTop models:")
    for (region, model_id), total in sorted(totals_by_model.items(), key=lambda x: x[1], reverse=True)[:20]:
        print(f"{region} | {model_id} -> {total:,}")

    total_tokens = int(sum(totals_by_metric.values()))
    return total_tokens, totals_by_metric

def update_gist(formatted_total: str):
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
    total_tokens, breakdown = get_bedrock_tokens()
    formatted_total = f"{total_tokens:,}"

    print(f"\nFinal Aggregation -> Exact Total: {formatted_total}")
    print(f"Final Breakdown -> {dict(breakdown)}")

    update_gist(formatted_total)

if __name__ == "__main__":
    main()
