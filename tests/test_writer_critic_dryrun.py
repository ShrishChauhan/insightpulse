"""Dry-run: fake insight -> WriterAgent -> CriticAgent. No real scraping or embedding.

Run from project root:
    python tests/test_writer_critic_dryrun.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import SupabaseClient
from core.llm_client import LLMClient
from agents.writer import WriterAgent
from agents.critic import CriticAgent

# ---------------------------------------------------------------------------
# Fake InsightResult (no scraping, no embedding, no retrieval)
# ---------------------------------------------------------------------------

FAKE_INSIGHT = {
    "topic": "playlist discovery",
    "company": "Spotify",
    "insight": {
        "topic": "playlist discovery",
        "company": "Spotify",
        "time_period": "last 7 days",
        "overall_sentiment_score": -0.42,
        "sentiment_label": "negative",
        "pain_points": [
            {
                "title": "Algorithmic playlists feel repetitive",
                "description": (
                    "Users report Discover Weekly recycling the same 20-30 tracks "
                    "after a few weeks, killing the sense of discovery."
                ),
                "evidence_quotes": [
                    "Discover Weekly has been the same songs for 3 months straight",
                    "Algorithm is stuck in a loop -- I keep hearing the same artists",
                ],
                "frequency": "very_frequent",
                "severity": "high",
            },
            {
                "title": "No collaborative playlist discovery features",
                "description": (
                    "Users want a way to discover playlists built by friends or "
                    "curated by people with similar taste, not just Spotify's editors."
                ),
                "evidence_quotes": [
                    "I want to see what playlists my friends actually listen to",
                    "Why can't I browse playlists by people with my taste profile",
                ],
                "frequency": "frequent",
                "severity": "medium",
            },
            {
                "title": "Search ranking buries independent artists",
                "description": (
                    "Playlist search results prioritize major-label content; "
                    "independent artists say their releases don't surface organically."
                ),
                "evidence_quotes": [
                    "Searched for 'indie jazz' and got Drake in the first result",
                    "Spotify search is just a major label advertising channel now",
                ],
                "frequency": "occasional",
                "severity": "high",
            },
        ],
        "positive_signals": [
            "AI DJ feature praised for smooth transitions",
            "Offline mode reliability improvements noticed",
        ],
        "competitor_mentions": [
            {"name": "Apple Music", "context": "users cite better human curation"},
            {"name": "YouTube Music", "context": "mentioned for broader catalog access"},
        ],
        "data_sources": ["r/spotify", "r/listentothis", "HN"],
        "source_count": 14,
        "confidence": "high",
    },
    "source_count": 14,
    "confidence": "high",
    "tokens_used": 0,
}


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run() -> None:
    """Execute the full Writer -> Critic chain and print results."""
    print("=== InsightPulse dry-run: Writer + Critic ===\n")

    db = SupabaseClient()
    llm = LLMClient(db=db)
    writer = WriterAgent(llm=llm, db=db)
    critic = CriticAgent(llm=llm, db=db)

    # 1. Generate LinkedIn post + PM brief
    print("[1/3] Calling WriterAgent.generate_both()...")
    writer_output = writer.generate_both(FAKE_INSIGHT)  # type: ignore[arg-type]

    post = writer_output["post_draft"]
    brief = writer_output["pm_brief"]

    print("\n--- LinkedIn Post ---")
    print(post["linkedin_post"])
    print(f"\nChars: {post['character_count']} | Hashtags: {post['hashtags']}")
    print(f"Hook: {post['hook_line']}")
    print(f"Engagement estimate: {post['estimated_engagement']}")

    print("\n--- PM Brief title ---")
    print(brief["title"])
    print(f"Executive summary: {brief['executive_summary']}")
    print(f"Proposed feature: {brief['proposed_feature'].get('name', 'N/A')}")
    print(f"Effort: {brief['proposed_feature'].get('effort_estimate', 'N/A')} | "
          f"Impact: {brief['proposed_feature'].get('impact_estimate', 'N/A')}")

    # 2. Score with Critic
    print("\n[2/3] Calling CriticAgent.evaluate()...")
    critic_result = critic.evaluate(
        post_draft=post,
        insight=FAKE_INSIGHT,  # type: ignore[arg-type]
    )

    print("\n--- Critic Scores ---")
    for field, score in critic_result["scores"].items():
        print(f"  {field}: {score}/5")
    print(f"  TOTAL: {critic_result['total']}/25")
    print(f"  Decision: {critic_result['decision']}")
    print(f"  Hallucination check: {critic_result['hallucination_check']}")
    print(f"  Primary weakness: {critic_result['primary_weakness']}")
    print(f"  Suggestion: {critic_result['improvement_suggestion']}")

    # 3. Notify if soft_approval
    print("\n[3/3] Checking notify_if_needed()...")
    notified = critic.notify_if_needed(critic_result)
    print(f"  Notification sent: {notified}")

    print("\n=== Dry-run complete ===")


if __name__ == "__main__":
    run()
