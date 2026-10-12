"""Command Line Interface for SEO Keyword-Ranking Collector.

Usage:
  python cli.py --keyword "best company in Coimbatore" --pages 2
"""
import argparse
import sys
import logging
from pathlib import Path

# Add package root and detector root to sys.path
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
DETECTOR_DIR = PROJECT_ROOT / "seo-captcha-detector"

for path_to_add in (PACKAGE_DIR, PROJECT_ROOT, DETECTOR_DIR):
    if str(path_to_add) not in sys.path:
        sys.path.insert(0, str(path_to_add))

from collector import SERPCollector

try:
    from detect_captcha import run_detection
except ImportError:
    try:
        from detect import run_detection
    except ImportError:
        run_detection = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    parser = argparse.ArgumentParser(description="SEO Keyword-Ranking Collector CLI (Connected to detect.py)")
    parser.add_argument("--keyword", type=str, default="best company in Coimbatore", help="Keyword search query")
    parser.add_argument("--pages", type=int, default=2, help="Number of SERP pages to crawl")
    parser.add_argument("--engine", type=str, default="google", choices=["google", "bing"], help="Search engine (google, bing)")
    parser.add_argument("--mode", type=str, default="browser", choices=["browser", "http"], help="Acquisition mode: browser (CloakBrowser) or http (curl_cffi)")
    parser.add_argument("--headed", action="store_true", help="Launch browser with visible window (headed mode)")
    parser.add_argument("--location", type=str, default=None, help="Target location (e.g. Coimbatore or Austin, TX)")
    parser.add_argument("--country", type=str, default="us", help="Target country code (e.g. us, in, uk)")
    parser.add_argument("--target", type=str, default=None, help="Target domain or URL to find & rank (e.g. campdigital.com)")
    parser.add_argument("--solver-url", type=str, default="http://127.0.0.1:5001", help="Sidecar solver URL")
    parser.add_argument("--detect-url", type=str, default=None, help="Run standalone CAPTCHA detection and resolution on a specific URL using detect.py")
    parser.add_argument("--inspect-captcha", action="store_true", help="Run deep diagnostic detection via detect.py on blocked SERP URLs")
    parser.add_argument("--page-delay", type=float, default=1.5, help="Pacing delay (seconds) between multi-page crawls to mimic natural human browsing")
    parser.add_argument("--no-persist-cookies", action="store_true", help="Disable persistent browser cookie profile (run incognito)")

    args = parser.parse_args()

    # If --detect-url is supplied, directly invoke detect.py / detect_captcha.py
    if args.detect_url:
        print("=" * 70)
        print(f"  SEO CAPTCHA Detector (via detect.py)")
        print(f"  Target: {args.detect_url}")
        print("=" * 70)
        if run_detection:
            result = run_detection(args.detect_url, solver_url=args.solver_url)
            return 0 if result.status == "success" else 1
        else:
            print("Error: Could not import run_detection from detect.py / detect_captcha.py")
            return 1

    clean_target = None
    if args.target:
        clean_target = args.target.lower().strip()
        clean_target = clean_target.replace("https://", "").replace("http://", "").split("/")[0].replace("www.", "")

    resolved_location = args.location
    if args.location:
        try:
            from geo_resolver import resolve_canonical_location
            resolved_location = resolve_canonical_location(args.location, country=args.country)
        except Exception:
            resolved_location = args.location

    collector = SERPCollector(
        engine=args.engine,
        mode=args.mode,
        headless=not args.headed,
        solver_url=args.solver_url,
        page_delay=args.page_delay,
        persist_cookies=not args.no_persist_cookies,
    )

    print("=" * 70)
    print(f"  SEO Keyword-Ranking Collector")
    print(f"  Keyword:     {args.keyword}")
    if clean_target:
        print(f"  Target:      {clean_target} ({args.target})")
    print(f"  Engine:      {args.engine.upper()}")
    print(f"  Mode:        {args.mode.upper()} {'(HEADED)' if args.headed else '(HEADLESS)'}")
    print(f"  Pages:       {args.pages}")
    print(f"  Page Delay:  {args.page_delay}s (polite human pacing)")
    if args.mode == "browser":
        print(f"  Cookies:     {'PERSISTENT (data/.browser_profile)' if not args.no_persist_cookies else 'INCOGNITO'}")
    print(f"  Detector:    CONNECTED (seo-captcha-detector/detect.py)")
    if resolved_location:
        print(f"  Location:    {resolved_location}")
        if resolved_location != args.location:
            print(f"  (Input Loc): {args.location}")
    print(f"  Country:     {args.country.upper()}")
    print("=" * 70)

    snapshot, deltas = collector.collect(
        keyword=args.keyword,
        max_pages=args.pages,
        location=resolved_location,
        country=args.country,
        target_domain=clean_target,
        compare_previous=True,
    )

    print("\n" + "=" * 70)
    print("  RANKING SNAPSHOT DATASET")
    print("=" * 70)
    print(f"  Snapshot ID:           {snapshot.snapshot_id}")
    print(f"  Pages Requested:       {snapshot.pages_requested}")
    print(f"  Pages Crawled:         {snapshot.pages_crawled}")
    print(f"  Pages Failed:          {snapshot.pages_failed}")
    print(f"  Pages Skipped:         {snapshot.pages_skipped}")
    print(f"  Total Organic Results: {snapshot.total_organic_results}")
    print(f"  Total Items Extracted: {len(snapshot.items)}")
    print("-" * 70)
    print(f"  Acquisition Status:    {snapshot.acquisition_status}")
    if snapshot.reason_code:
        print(f"  Reason Code:           {snapshot.reason_code}")
    if snapshot.block_reason:
        print(f"  Block Reason:          {snapshot.block_reason}")
    if snapshot.failure_reason:
        print(f"  Failure:               {snapshot.failure_reason}")
    challenge_display = "NONE" if (not snapshot.challenge_type or snapshot.challenge_type in ("none_detected", "no_challenge")) else snapshot.challenge_type
    print(f"  Challenge Detected:    {challenge_display}")

    matched_item = None
    if clean_target and snapshot.items:
        for item in snapshot.items:
            item_domain = (item.domain or "").lower().replace("www.", "")
            if clean_target in item_domain or clean_target in (item.canonical_url or "").lower():
                matched_item = item
                break

        if matched_item:
            rank_no = matched_item.organic_position or matched_item.absolute_position
            print("\n" + "=" * 70)
            print(f"  >>> TARGET MATCH FOUND!")
            print(f"  Target:       {clean_target}")
            print(f"  Rank:         #{rank_no}")
            print(f"  Matched URL:  {matched_item.canonical_url}")
            print(f"  Title:        {matched_item.title}")
            print("=" * 70)
        else:
            print("\n" + "-" * 70)
            print(f"  >>> TARGET NOT IN TOP {len(snapshot.items)} ORGANIC RESULTS")
            print(f"  Domain '{clean_target}' was not found in the {snapshot.pages_crawled} page(s) crawled.")
            print("-" * 70)

    if snapshot.pages_crawled == 0 or len(snapshot.items) == 0:
        print("\n  Ranking Data:          NOT AVAILABLE")
    else:
        print("\n  Top Organic Rankings:")
        print(f"  {'Org #':<6} | {'Abs #':<6} | {'Domain':<25} | {'URL'}")
        print("  " + "-" * 66)

        for item in snapshot.items:
            org_str = str(item.organic_position) if item.organic_position else "n/a"
            marker = " <== TARGET" if (matched_item and item == matched_item) else ""
            print(f"  {org_str:<6} | {item.absolute_position:<6} | {item.domain[:25]:<25} | {item.canonical_url[:40]}{marker}")

        if deltas:
            print("\n" + "-" * 70)
            print("  RANK DELTAS COMPARED TO PREVIOUS SNAPSHOT")
            print("-" * 70)
            for delta in deltas:
                change_str = f"[{delta.change.upper()}]"
                d_str = f"({delta.delta:+d})" if delta.delta is not None else ""
                print(f"  {change_str:<12} {delta.domain:<25} prev: {str(delta.previous_position):<4} curr: {str(delta.current_position):<4} {d_str}")

    print("\n" + "=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
