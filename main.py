"""Entry point: APScheduler cron wiring and orchestrator trigger."""

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _build_orchestrator():
    """Import and instantiate OrchestratorAgent (deferred to avoid slow startup)."""
    from agents.orchestrator import OrchestratorAgent
    return OrchestratorAgent()


def _daily_ingest_job() -> None:
    """Scrape all sources and embed results into Supabase."""
    print("[scheduler] Starting daily scrape + embed...")
    from core.scraper import scrape_all
    from core.embedder import Embedder

    scraped = scrape_all()
    all_posts = []
    for source, posts in scraped.items():
        all_posts.extend(posts)
    print(f"[scheduler] Scraped {len(all_posts)} posts")

    embedder = Embedder()
    result = embedder.embed_batch(all_posts)
    print(f"[scheduler] Embedded: {result}")


def run_scheduler() -> None:
    """Start APScheduler: run_weekly() every Monday and Thursday at 9 AM."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from tools.notifier import Notifier

    orchestrator = _build_orchestrator()
    notifier = Notifier()
    scheduler = BlockingScheduler(timezone="UTC")

    scheduler.add_job(
        func=_daily_ingest_job,
        trigger="cron",
        hour=6,
        minute=0,
        id="insightpulse_daily_ingest",
    )

    scheduler.add_job(
        func=lambda: orchestrator.run_weekly(dry_run=False),
        trigger="cron",
        day_of_week="mon,thu",
        hour=9,
        minute=0,
        id="insightpulse_weekly",
    )

    def _weekly_digest_job() -> None:
        from datetime import datetime, timezone, timedelta
        from core.db import SupabaseClient as _DB
        digest_db = _DB()
        stats = digest_db.get_weekly_post_stats()
        now = datetime.now(timezone.utc)
        candidates = []
        for weekday in (0, 3):  # Monday=0, Thursday=3
            delta = (weekday - now.weekday()) % 7 or 7
            next_dt = (now + timedelta(days=delta)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )
            candidates.append(next_dt)
        stats["next_run_time"] = min(candidates).strftime("%A %Y-%m-%d 09:00 UTC")
        notifier.notify_weekly_digest(stats)
        print(
            f"[digest] Weekly digest sent. "
            f"posts={stats['posts_sent']} avg={stats['avg_critic_score']}"
        )

    scheduler.add_job(
        func=_weekly_digest_job,
        trigger="cron",
        day_of_week="sun",
        hour=20,
        minute=0,
        id="insightpulse_weekly_digest",
    )

    print("[main] InsightPulse scheduler started.")
    print("[main] Daily ingest: every day at 06:00 UTC.")
    print("[main] Pipeline: Monday + Thursday at 09:00 UTC.")
    print("[main] Weekly digest: every Sunday at 20:00 UTC.")
    print("[main] Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("[main] Scheduler stopped.")


def _print_result(result: dict) -> None:
    """Print RunResult fields as an ASCII summary."""
    print("\n--- RunResult ---")
    print(f"  run_id          : {result['run_id']}")
    print(f"  status          : {result['status']}")
    print(f"  topics_processed: {result['topics_processed']}")
    print(f"  posts_created   : {result['posts_created']}")
    print(f"  duration_ms     : {result['duration_ms']}")
    print(f"  dry_run         : {result['dry_run']}")
    if result["errors"]:
        print(f"  errors ({len(result['errors'])}):")
        for e in result["errors"]:
            print(f"    - {e}")
    else:
        print("  errors          : none")
    print("-----------------\n")


def main() -> None:
    """Parse CLI args and dispatch to the correct run mode."""
    parser = argparse.ArgumentParser(
        prog="insightpulse",
        description="InsightPulse — autonomous LinkedIn post generator",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run in dry-run mode (no live LinkedIn posting)",
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=None,
        help="Run for a specific topic (e.g. 'Spotify pricing')",
    )
    parser.add_argument(
        "--company",
        type=str,
        default=None,
        help="Company name matching the topic (e.g. 'spotify')",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        default=False,
        help="Start the APScheduler cron (blocking)",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        default=False,
        help="Run scrape + embed pipeline immediately and exit",
    )

    args = parser.parse_args()

    # Startup log
    from core.db import SupabaseClient
    db = SupabaseClient()
    db.log_run(
        agent_name="main",
        status="success",
        input_summary=(
            f"startup dry_run={args.dry_run} "
            f"topic={args.topic} company={args.company} "
            f"schedule={args.schedule} ingest={args.ingest}"
        ),
        output_summary="InsightPulse started",
    )
    print("[main] InsightPulse started.")

    if args.ingest:
        _daily_ingest_job()
        sys.exit(0)

    if args.schedule:
        run_scheduler()
        return

    orchestrator = _build_orchestrator()

    # Generate graph image as portfolio artifact
    orchestrator.get_graph_image()

    if args.topic and args.company:
        print(f"[main] run_single: topic='{args.topic}' company='{args.company}' dry_run={args.dry_run}")
        result = orchestrator.run_single(
            topic=args.topic,
            company=args.company,
            dry_run=args.dry_run,
        )
    elif args.topic and not args.company:
        print("[main] ERROR: --topic requires --company. Example: --topic 'Spotify pricing' --company spotify")
        sys.exit(1)
    else:
        print(f"[main] run_weekly dry_run={args.dry_run}")
        result = orchestrator.run_weekly(dry_run=args.dry_run)

    _print_result(result)


if __name__ == "__main__":
    main()
