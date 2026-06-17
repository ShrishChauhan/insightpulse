"""CLI for on-demand Gold Mining: search Google for Reddit pain threads and embed them.

Usage:
    python gold_mine.py --company spotify
    python gold_mine.py --company spotify --queries '"Spotify" "hate"' '"Spotify" "broken"'
    python gold_mine.py --all
    python gold_mine.py --company spotify --no-embed
"""

import argparse
import sys
import time


def _print_post(post: dict, idx: int) -> None:
    title = post["title"].encode("ascii", errors="replace").decode("ascii")
    url = post["url"]
    score = post["score"]
    comments = len(post.get("comments", []))
    tags = ", ".join(post["company_tags"]) or "none"
    subreddit = post["subreddit"]
    print(f"  [{idx:>2}] score={score:>5}  comments={comments}  tags={tags}")
    print(f"       {subreddit}: {title[:80]}")
    print(f"       {url[:90]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="gold_mine",
        description="Gold Mining: targeted Reddit pain-point scraper via Serper + .json",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--company",
        type=str,
        help="Company to mine (e.g. spotify, apple, notion)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Mine all companies in config.GOLD_MINING_QUERIES",
    )
    parser.add_argument(
        "--queries",
        nargs="+",
        default=None,
        metavar="QUERY",
        help="Override default query templates (ignored with --all)",
    )
    parser.add_argument(
        "--no-embed",
        action="store_true",
        default=False,
        help="Scrape only; skip embedding into Supabase",
    )

    args = parser.parse_args()

    import config
    from core.scraper import GoldMiningScraper

    if not config.SERPER_API_KEY:
        print("[gold_mine] ERROR: SERPER_API_KEY not set in .env")
        print("  Sign up at serper.dev (free, no credit card) and add the key.")
        sys.exit(1)

    scraper = GoldMiningScraper()
    start = time.time()

    if args.all:
        print(f"[gold_mine] Mining all {len(config.GOLD_MINING_QUERIES)} companies...")
        posts = scraper.scrape()
    else:
        company = args.company.lower()
        if company not in config.GOLD_MINING_QUERIES and not args.queries:
            print(f"[gold_mine] WARNING: '{company}' not in GOLD_MINING_QUERIES and no --queries given")
        print(f"[gold_mine] Mining '{company}'...")
        posts = scraper.mine(company, query_overrides=args.queries)

    elapsed = time.time() - start
    print(f"\n[gold_mine] Done in {elapsed:.1f}s -- {len(posts)} threads scraped\n")

    if not posts:
        print("[gold_mine] No threads found. Check SERPER_API_KEY and try again.")
        sys.exit(0)

    for i, post in enumerate(posts, 1):
        _print_post(post, i)
    print()

    if args.no_embed:
        print("[gold_mine] --no-embed: skipping Supabase ingestion.")
        sys.exit(0)

    print(f"[gold_mine] Embedding {len(posts)} posts into Supabase...")
    from core.embedder import Embedder
    result = Embedder().embed_batch(posts)
    print(f"[gold_mine] Embedded: {result}")


if __name__ == "__main__":
    main()
