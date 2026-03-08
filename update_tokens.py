import os
import requests
import boto3
from datetime import datetime, timedelta, timezone

# Configure the source regions you actually invoke Bedrock from.
# Example:
# BEDROCK_SOURCE_REGIONS=us-east-1,us-east-2,us-west-2
REGIONS = [
    r.strip()
    for r in os.getenv("BEDROCK_SOURCE_REGIONS", "us-east-1,us-east-2,us-west-2").split(",")
    if r.strip()
]

METRICS = [
    ("input", "InputTokenCount"),
    ("output", "OutputTokenCount"),
    ("cache_read", "CacheReadInputTokens"),
    ("cache_write", "CacheWriteInputTokens"),
]

def fetch_all_metric_pages(cloudwatch, start_time, end_time):
    next_token = None
    total_by_metric = {key: 0 for key, _ in METRICS}

    while True:
        queries = []
        for i, (key, metric_name) in enumerate(METRICS):
            queries.append(
                {
                    "Id": f"q{i}",
                    "Expression": (
                        f"SEARCH('{{AWS/Bedrock,ModelId}} "
                        f'MetricName="{metric_name}"\', \'Sum\', 86400)'
                    ),
                    "ReturnData": True,
                }
            )

        kwargs = {
            "MetricDataQueries": queries,
            "StartTime": start_time,
            "EndTime": end_time,
            "ScanBy": "TimestampAscending",
        }
        if next_token:
            kwargs["NextToken"] = next_token

        response = cloudwatch.get_metric_data(**kwargs)

        for result in response.get("MetricDataResults", []):
            result_id = result.get("Id")
            values = result.get("Values", [])
            if not values:
                continue

            if result_id == "q0":
                total_by_metric["input"] += sum(values)
            elif result_id == "q1":
                total_by_metric["output"] += sum(values)
            elif result_id == "q2":
                total_by_metric["cache_read"] += sum(values)
            elif result_id == "q3":
                total_by_metric["cache_write"] += sum(values)

        next_token = response.get("NextToken")
        if not next_token:
            break

    return total_by_metric


def get_bedrock_tokens():
    # 365 days is fine for now, but if you want "all-time", store a rolling total
    # or use invocation logs as the source of truth.
    start_time = datetime.now(timezone.utc) - timedelta(days=365)
    end_time = datetime.now(timezone.utc)

    grand_totals = {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
    }

    for region in REGIONS:
        try:
            cloudwatch = boto3.client("cloudwatch", region_name=region)
            region_totals = fetch_all_metric_pages(cloudwatch, start_time, end_time)

            print(
                f"{region} -> "
                f"input={int(region_totals['input']):,}, "
                f"output={int(region_totals['output']):,}, "
                f"cache_read={int(region_totals['cache_read']):,}, "
                f"cache_write={int(region_totals['cache_write']):,}"
            )

            for key in grand_totals:
                grand_totals[key] += region_totals[key]

        except Exception as e:
            print(f"Error fetching metrics from {region}: {e}")

    total_processed_tokens = int(
        grand_totals["input"]
        + grand_totals["output"]
        + grand_totals["cache_read"]
        + grand_totals["cache_write"]
    )

    return total_processed_tokens, grand_totals


aws_total_tokens, breakdown = get_bedrock_tokens()

# Remove the fake 95% adjustment.
formatted_total = f"{aws_total_tokens:,}"
print(f"\nFinal Aggregation -> Exact Total: {formatted_total}")

svg_content = f"""<svg width="400" height="120" viewBox="0 0 400 120" xmlns="http://www.w3.org/2000/svg">
  <rect width="400" height="120" rx="10" fill="#0d1117" stroke="#30363d" stroke-width="2"/>
  <text x="25" y="40" font-family="Arial, sans-serif" font-size="14" font-weight="bold" fill="#8b949e">TOKENS SACRIFICED TO LLM GODS</text>
  <defs>
    <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#6C3AED;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#3182ce;stop-opacity:1" />
    </linearGradient>
  </defs>
  <text x="25" y="90" font-family="Arial, sans-serif" font-size="36" font-weight="bold" fill="url(#grad)">{formatted_total}</text>
</svg>"""

GIST_ID = os.environ.get("GIST_ID")
GITHUB_TOKEN = os.environ.get("GH_PAT")

if not GIST_ID or not GITHUB_TOKEN:
    raise ValueError("Missing GIST_ID or GH_PAT environment variables.")

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}
payload = {"files": {"tokens.svg": {"content": svg_content}}}

response = requests.patch(
    f"https://api.github.com/gists/{GIST_ID}",
    headers=headers,
    json=payload,
)

if response.status_code == 200:
    print("Success! Gist updated with the new model metrics.")
else:
    print(f"Failed to update gist: {response.text}")
