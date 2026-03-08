import os
import boto3
from datetime import datetime, timedelta

REGIONS = [
    r.strip() for r in os.getenv(
        "BEDROCK_SOURCE_REGIONS",
        "us-east-1,us-east-2,us-west-2"
    ).split(",") if r.strip()
]

MODEL_IDS = [
    m.strip() for m in os.getenv(
        "BEDROCK_MODEL_IDS",
        ",".join([
            # fill these with the exact Bedrock model IDs you actually used
            # example placeholders only:
            "anthropic.claude-opus-4-6-20260205-v1:0",
            "anthropic.claude-sonnet-4-6-20260205-v1:0",
            "anthropic.claude-opus-4-5-20251124-v1:0",
        ])
    ).split(",") if m.strip()
]

METRICS = [
    "InputTokenCount",
    "OutputTokenCount",
    "CacheReadInputTokens",
    "CacheWriteInputTokens",
]

def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def get_bedrock_tokens():
    start_time = datetime.utcnow() - timedelta(days=60)
    end_time = datetime.utcnow()

    totals = {metric: 0 for metric in METRICS}

    for region in REGIONS:
        cw = boto3.client("cloudwatch", region_name=region)

        queries = []
        idx = 0
        for model_id in MODEL_IDS:
            for metric_name in METRICS:
                queries.append({
                    "Id": f"m{idx}",
                    "Label": f"{region}|{model_id}|{metric_name}",
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
                })
                idx += 1

        for batch in chunks(queries, 500):
            next_token = None
            while True:
                params = {
                    "MetricDataQueries": batch,
                    "StartTime": start_time,
                    "EndTime": end_time,
                }
                if next_token:
                    params["NextToken"] = next_token

                response = cw.get_metric_data(**params)

                for result in response.get("MetricDataResults", []):
                    label = result.get("Label", "")
                    metric_name = label.split("|")[-1]
                    totals[metric_name] += sum(result.get("Values", []))

                next_token = response.get("NextToken")
                if not next_token:
                    break

    total_tokens = int(sum(totals.values()))
    return total_tokens, {k: int(v) for k, v in totals.items()}

aws_total_tokens, breakdown = get_bedrock_tokens()

print("Breakdown:", breakdown)
print("Total:", f"{aws_total_tokens:,}")
