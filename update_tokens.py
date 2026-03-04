import os
import requests
import boto3
from datetime import datetime, timedelta

# --- 1. Pull Live AWS Bedrock Metrics (Multi-Region) ---
def get_bedrock_tokens():
    # We will check the top Bedrock regions so we don't miss any tokens
    regions = ['us-east-1', 'us-west-2', 'us-east-2'] 
    aws_total = 0
    
    start_time = datetime.utcnow() - timedelta(days=365)
    end_time = datetime.utcnow()
    
    for region in regions:
        try:
            cloudwatch = boto3.client('cloudwatch', region_name=region) 
            
            response = cloudwatch.get_metric_data(
                MetricDataQueries=[
                    {
                        'Id': 'input_tokens',
                        'Expression': "SEARCH('{AWS/Bedrock,ModelId} MetricName=\"InputTokenCount\"', 'Sum', 2592000)",
                        'ReturnData': True,
                    },
                    {
                        'Id': 'output_tokens',
                        'Expression': "SEARCH('{AWS/Bedrock,ModelId} MetricName=\"OutputTokenCount\"', 'Sum', 2592000)",
                        'ReturnData': True,
                    }
                ],
                StartTime=start_time,
                EndTime=end_time,
            )
            
            region_total = 0
            for result in response.get('MetricDataResults', []):
                if result['Values']:
                    region_total += sum(result['Values'])
            
            print(f"Tokens found in {region}: {int(region_total)}")
            aws_total += region_total
            
        except Exception as e:
            print(f"Error fetching metrics from {region}: {e}")

    return int(aws_total)

# --- 2. Calculate Total (AWS = 95%) ---
aws_tokens = get_bedrock_tokens()

# Calculate the 100% total assuming AWS makes up 95% of your usage
total_tokens = int(aws_tokens / 0.95) if aws_tokens > 0 else 0

# --- 3. Format the Number ---
if total_tokens >= 1000000000:
    formatted_total = f"{total_tokens/1000000000:.1f}B"
elif total_tokens >= 1000000:
    formatted_total = f"{total_tokens/1000000:.1f}M"
elif total_tokens >= 1000:
    formatted_total = f"{total_tokens/1000:.1f}K"
else:
    formatted_total = str(total_tokens)

print(f"\nFinal Aggregation -> AWS Tokens: {aws_tokens} | Estimated Total: {total_tokens} ({formatted_total})")

# --- 4. Generate the Aesthetic SVG ---
svg_content = f"""<svg width="400" height="120" viewBox="0 0 400 120" xmlns="http://www.w3.org/2000/svg">
  <rect width="400" height="120" rx="10" fill="#0d1117" stroke="#30363d" stroke-width="2"/>
  <text x="30" y="40" font-family="Arial, sans-serif" font-size="14" font-weight="bold" fill="#8b949e">TOKENS SACRIFICED TO LLM GODS</text>
  <defs>
    <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#6C3AED;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#3182ce;stop-opacity:1" />
    </linearGradient>
  </defs>
  <text x="30" y="90" font-family="Arial, sans-serif" font-size="48" font-weight="bold" fill="url(#grad)">{formatted_total}</text>
</svg>"""

# --- 5. Push to GitHub Gist ---
GIST_ID = os.environ.get("GIST_ID")
GITHUB_TOKEN = os.environ.get("GH_PAT")

if not GIST_ID or not GITHUB_TOKEN:
    raise ValueError("Missing GIST_ID or GH_PAT environment variables.")

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}

payload = {
    "files": {
        "tokens.svg": {
            "content": svg_content
        }
    }
}

response = requests.patch(f"https://api.github.com/gists/{GIST_ID}", headers=headers, json=payload)

if response.status_code == 200:
    print("Success! Gist updated.")
else:
    print(f"Failed to update gist: {response.text}")
