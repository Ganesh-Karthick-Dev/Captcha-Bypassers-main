"""Direct in-process solver example (Method B).

Calls captcha-solver-main internal modules directly without needing
the HTTP server (server.py) running.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure captcha-solver-main root directory is in sys.path
SOLVER_ROOT = Path(__file__).resolve().parent.parent
if str(SOLVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SOLVER_ROOT))

# Direct solver imports from captcha-solver-main
from recaptcha.solve import solve_recaptcha_v2


async def solve_direct(sitekey: str, url: str):
    print("=" * 60)
    print("  Direct Solver (Method B - In-Process)")
    print(f"  Target URL: {url}")
    print(f"  Sitekey:    {sitekey}")
    print("=" * 60)

    print("\n[*] Invoking solve_recaptcha_v2 directly via CloakBrowser...")
    result = await solve_recaptcha_v2(
        sitekey=sitekey,
        url=url,
        classifier="auto",  # Uses local YOLO/ONNX tile classifier
    )

    token = result.get("token")
    elapsed = result.get("elapsed")
    error = result.get("error")

    print("\n" + "-" * 60)
    print("  RESULT")
    print("-" * 60)
    if token:
        print(f"  Status:       SUCCESS")
        print(f"  Elapsed:      {elapsed}s")
        print(f"  Token Length: {len(token)} chars")
        print(f"  Token Prefix: {token[:40]}...")
        print(f"  Token Suffix: ...{token[-20:]}")
    else:
        print(f"  Status:       FAILED")
        print(f"  Error:        {error}")
    print("=" * 60)

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Direct in-process solver example (Method B)")
    parser.add_argument("positional", nargs="*", help="[sitekey] [url]")
    parser.add_argument("--url", type=str, help="Target URL")
    parser.add_argument("--sitekey", type=str, help="Target sitekey")

    args = parser.parse_args()

    sitekey = args.sitekey
    url = args.url

    # Fallback to positional arguments if --url or --sitekey are not explicitly specified
    if not sitekey and len(args.positional) > 0:
        sitekey = args.positional[0]
    if not url and len(args.positional) > 1:
        url = args.positional[1]
    elif not url and sitekey and (sitekey.startswith("http://") or sitekey.startswith("https://") or "." in sitekey):
        # If user passed a single positional arg that looks like a URL
        url = sitekey
        sitekey = None

    # Defaults
    if not sitekey:
        sitekey = "6LeXh6gtAAAAAJOjvYe7bLydr1vPFEMiIqsgCOUw"
    if not url:
        url = "https://ermisglobal.com/"

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    asyncio.run(solve_direct(sitekey=sitekey, url=url))
