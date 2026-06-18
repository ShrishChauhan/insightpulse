"""Chunking and pgvector storage for InsightPulse.

Chunks ScrapedPost content by sentence boundary, embeds via
sentence-transformers (all-MiniLM-L6-v2, local/free), and stores to
Supabase pgvector via core/db.py. Never calls Supabase directly.
"""

import os
import re
import time

import tiktoken
from sentence_transformers import SentenceTransformer
from typing_extensions import TypedDict

import config
from core.db import SupabaseClient
from core.scraper import ScrapedPost


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

class EmbedResult(TypedDict):
    """Result returned by embed_batch()."""
    embedded_count: int
    skipped_count: int
    error_count: int
    duration_ms: int


# ---------------------------------------------------------------------------
# Embedder
# ---------------------------------------------------------------------------

class Embedder:
    """Chunks ScrapedPost content and stores embeddings to Supabase pgvector."""

    _AGENT_NAME = "embedder"
    _SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')

    def __init__(self) -> None:
        """Load sentence-transformer model and tiktoken encoder."""
        hf_token = os.getenv("HF_TOKEN")
        if hf_token:
            from huggingface_hub import login
            login(token=hf_token)
        self._model = SentenceTransformer(config.EMBEDDING_MODEL)
        self._enc = tiktoken.get_encoding("cl100k_base")
        self._db = SupabaseClient()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_batch(self, posts: list[ScrapedPost]) -> EmbedResult:
        """Chunk, embed, and store a list of ScrapedPost objects.

        Processes embedding in batches of EMBED_BATCH_SIZE. Logs run to Supabase.
        """
        start = time.time()
        embedded = skipped = errors = 0
        duration = 0

        try:
            all_chunks: list[dict] = []
            for post in posts:
                try:
                    all_chunks.extend(self._chunk_post(post))
                except Exception as exc:
                    print(f"[embedder] chunk error {post.get('url', '?')}: {exc}")
                    errors += 1

            for i in range(0, len(all_chunks), config.EMBED_BATCH_SIZE):
                batch = all_chunks[i : i + config.EMBED_BATCH_SIZE]
                texts = [c["content"] for c in batch]
                try:
                    vectors = self._model.encode(texts, show_progress_bar=False).tolist()
                    items = [
                        {"content": c["content"], "embedding": v, "metadata": c["metadata"]}
                        for c, v in zip(batch, vectors)
                    ]
                    ins, skip = self._db.bulk_store_embeddings(items)
                    embedded += ins
                    skipped += skip
                except Exception as exc:
                    print(f"[embedder] embed/store error batch {i // config.EMBED_BATCH_SIZE}: {exc}")
                    errors += len(batch)

        finally:
            duration = int((time.time() - start) * 1000)
            self._db.log_run(
                agent_name=self._AGENT_NAME,
                status="success" if errors == 0 else "failed",
                input_summary=f"{len(posts)} posts",
                output_summary=f"embedded={embedded} skipped={skipped} errors={errors}",
                duration_ms=duration,
            )

        return EmbedResult(
            embedded_count=embedded,
            skipped_count=skipped,
            error_count=errors,
            duration_ms=duration,
        )

    def get_collection_stats(self) -> dict:
        """Return total_vectors, oldest, newest, and by_company breakdown."""
        return self._db.get_embedding_stats()

    def delete_old_content(self, days: int = 30) -> int:
        """Delete embeddings older than N days; return deleted count."""
        return self._db.delete_old_embeddings(days=days)

    # ------------------------------------------------------------------
    # Chunking internals
    # ------------------------------------------------------------------

    def _build_combined_text(self, post: ScrapedPost) -> str:
        """Format post fields into single combined string for chunking.

        Structure: [TITLE]: ... \\n [BODY]: ... \\n [COMMENTS]: c1 | c2 ...
        Top 5 comments only (highest signal, keeps chunks focused).
        """
        top_comments = post["comments"][:5]
        parts = [
            f"[TITLE]: {post['title']}",
            f"[BODY]: {post['body']}",
        ]
        if top_comments:
            parts.append(f"[COMMENTS]: {' | '.join(top_comments)}")
        return "\n".join(parts)

    def _count_tokens(self, text: str) -> int:
        """Token count using cl100k_base encoding."""
        return len(self._enc.encode(text))

    def _chunk_post(self, post: ScrapedPost) -> list[dict]:
        """Split a post into sentence-boundary-aligned token chunks.

        Chunk size: config.CHUNK_SIZE tokens, overlap: config.CHUNK_OVERLAP tokens.
        Never splits mid-sentence unless a single sentence exceeds CHUNK_SIZE.
        """
        combined = self._build_combined_text(post)
        sentences = [s for s in self._SENTENCE_SPLIT.split(combined.strip()) if s]

        metadata = {
            "source_url": post["url"],
            "subreddit": post.get("subreddit", ""),
            "company_tags": post.get("company_tags", []),
            "post_score": post.get("score", 0),
        }

        chunks: list[dict] = []
        current: list[str] = []
        current_tokens = 0

        for sentence in sentences:
            s_tokens = self._count_tokens(sentence)

            # Sentence larger than chunk limit: hard-split on token boundary
            if s_tokens > config.CHUNK_SIZE:
                if current:
                    chunks.append({"content": " ".join(current), "metadata": metadata})
                    current, current_tokens = [], 0
                token_ids = self._enc.encode(sentence)
                for j in range(0, len(token_ids), config.CHUNK_SIZE):
                    chunk_text = self._enc.decode(token_ids[j : j + config.CHUNK_SIZE])
                    chunks.append({"content": chunk_text, "metadata": metadata})
                continue

            # Would overflow: save chunk, carry overlap into next
            if current_tokens + s_tokens > config.CHUNK_SIZE:
                if current:
                    chunks.append({"content": " ".join(current), "metadata": metadata})
                # Keep trailing sentences within CHUNK_OVERLAP tokens for context
                overlap: list[str] = []
                overlap_tokens = 0
                for s in reversed(current):
                    t = self._count_tokens(s)
                    if overlap_tokens + t > config.CHUNK_OVERLAP:
                        break
                    overlap.insert(0, s)
                    overlap_tokens += t
                current = overlap + [sentence]
                current_tokens = overlap_tokens + s_tokens
            else:
                current.append(sentence)
                current_tokens += s_tokens

        if current:
            chunks.append({"content": " ".join(current), "metadata": metadata})

        # Fallback: empty post body produces at least one chunk
        if not chunks:
            chunks.append({"content": combined[:500], "metadata": metadata})

        return chunks


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _make_fake_post(i: int) -> ScrapedPost:
    """Generate a synthetic ScrapedPost for testing."""
    return ScrapedPost(
        id=f"fake_{i}",
        title=f"Apple releases new AI feature in iOS {18 + i}",
        body=(
            f"Apple announced a major update to its AI pipeline today. "
            f"The new feature, codenamed Project Orion {i}, leverages on-device "
            f"models to deliver real-time suggestions. Privacy is maintained because "
            f"all inference happens locally. Developers will gain API access in beta. "
            f"The rollout is planned for Q{(i % 4) + 1} next year."
        ),
        comments=[
            f"This is a game changer for privacy-first AI. Comment {i}a.",
            f"Wondering how this compares to Google approach. Comment {i}b.",
            f"On-device inference is the future. Apple leads again. Comment {i}c.",
        ],
        subreddit="apple",
        score=150 + i * 10,
        created_utc=1_700_000_000.0 + i * 3600,
        url=f"https://example.com/post/{i}",
        company_tags=["apple"],
        source_type="reddit",
        source_name="r/apple",
    )


if __name__ == "__main__":
    import sys

    if "--real" in sys.argv:
        print("\n[real embed] Running scrape_all() + embed_batch()...")
        from core.scraper import scrape_all
        scraped = scrape_all()
        all_posts = []
        for source, posts in scraped.items():
            all_posts.extend(posts)
        print(f"[real embed] Total posts to embed: {len(all_posts)}")
        embedder = Embedder()
        result = embedder.embed_batch(all_posts)
        print(f"[real embed] embedded={result['embedded_count']} "
              f"skipped={result['skipped_count']} "
              f"errors={result['error_count']}")
        stats = embedder.get_collection_stats()
        print(f"[real embed] Total vectors now: {stats['total_vectors']}")
    else:
        print("[smoke test] initialising embedder...")
        embedder = Embedder()

        fake_posts = [_make_fake_post(i) for i in range(5)]
        print(f"[smoke test] embedding {len(fake_posts)} fake posts...")

        result = embedder.embed_batch(fake_posts)
        print(
            f"[smoke test] embed_batch: "
            f"embedded={result['embedded_count']} "
            f"skipped={result['skipped_count']} "
            f"errors={result['error_count']} "
            f"duration={result['duration_ms']}ms"
        )

        print("\n[smoke test] collection stats:")
        stats = embedder.get_collection_stats()
        print(f"  total_vectors : {stats['total_vectors']}")
        print(f"  oldest        : {stats['oldest']}")
        print(f"  newest        : {stats['newest']}")
        print(f"  by_company    : {stats['by_company']}")

        print("\n[smoke test] similarity search (top 3)...")
        db = SupabaseClient()
        query_vec = embedder._model.encode(
            "Apple on-device AI privacy", show_progress_bar=False
        ).tolist()
        hits = db.similarity_search(query_vec, match_count=3)
        print(f"  returned {len(hits)} hits:")
        for h in hits:
            preview = h.get("content", "")[:80].replace("\n", " ")
            sim = h.get("similarity", 0)
            print(f"    sim={sim:.4f}  {preview}...")

        print("\n[smoke test] PASS")
