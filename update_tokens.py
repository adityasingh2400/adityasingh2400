import os
import math
import requests

GIST_ID = os.environ.get("GIST_ID")
GITHUB_TOKEN = os.environ.get("GH_PAT")

if not GIST_ID or not GITHUB_TOKEN:
    raise ValueError("Missing GIST_ID or GH_PAT environment variables.")

# Main inputs
SPEND_USD = float(os.getenv("ESTIMATED_BEDROCK_SPEND_USD", "2700"))
TOKENS_PER_DOLLAR = float(os.getenv("TOKENS_PER_DOLLAR", "220000"))

# Optional: make the number look less like a round multiplication result
# This is still deterministic, not random.
FUDGE_BPS = int(os.getenv("ESTIMATE_FUDGE_BPS", "137"))  # 137 bps = 1.37%

def estimate_tokens(spend_usd: float, tokens_per_dollar: float, fudge_bps: int) -> int:
    raw = spend_usd * tokens_per_dollar
    adjusted = raw * (1 + fudge_bps / 10000.0)
    return int(math.floor(adjusted))

def update_gist(formatted_total: str):
    svg_content = f"""<svg width="430" height="120" viewBox="0 0 430 120" xmlns="http://www.w3.org/2000/svg">
  <rect width="430" height="120" rx="12" fill="#0d1117" stroke="#30363d" stroke-width="2"/>
  <text x="24" y="38" font-family="Arial, sans-serif" font-size="14" font-weight="bold" fill="#8b949e">ESTIMATED TOKENS SACRIFICED TO LLM GODS</text>
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
        print("Success! Gist updated.")
    else:
        raise RuntimeError(f"Failed to update gist: {response.status_code} {response.text}")

def main():
    estimated_tokens = estimate_tokens(
        spend_usd=SPEND_USD,
        tokens_per_dollar=TOKENS_PER_DOLLAR,
        fudge_bps=FUDGE_BPS,
    )

    print(f"SPEND_USD = {SPEND_USD}")
    print(f"TOKENS_PER_DOLLAR = {TOKENS_PER_DOLLAR}")
    print(f"FUDGE_BPS = {FUDGE_BPS}")
    print(f"ESTIMATED_TOTAL = {estimated_tokens:,}")

    update_gist(f"{estimated_tokens:,}")

if __name__ == "__main__":
    main()
