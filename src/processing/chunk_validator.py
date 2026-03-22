"""Chunk quality validation — filters out low-signal chunks before embedding."""

import re
from dataclasses import dataclass

from src.documents.chunker import ChunkData

# Thresholds
MIN_ALPHA_RATIO = 0.4       # at least 40 % of chars must be alphabetic
MAX_REPEATED_CHAR_RATIO = 0.3  # repeated runs of a single char must be < 30 %
MIN_UNIQUE_WORDS = 5        # need at least 5 distinct words
MIN_WORD_COUNT = 8          # need at least 8 words total
MAX_URL_RATIO = 0.6         # URL-heavy chunks (nav/header cruft) are skipped

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_WORD_RE = re.compile(r"\b\w+\b")


@dataclass
class ChunkQuality:
    is_valid: bool
    reason: str = ""
    quality_score: float = 1.0   # 0.0 – 1.0, stored on the Chunk row


class ChunkValidator:
    """Validates individual chunks and assigns quality scores."""

    def validate(self, chunk: ChunkData) -> ChunkQuality:
        text = chunk.content.strip()

        if not text:
            return ChunkQuality(is_valid=False, reason="empty", quality_score=0.0)

        # --- basic counts ------------------------------------------------
        total_chars = len(text)
        alpha_chars = sum(1 for c in text if c.isalpha())
        words = _WORD_RE.findall(text)
        word_count = len(words)
        unique_words = len(set(w.lower() for w in words))

        # --- alpha ratio -------------------------------------------------
        alpha_ratio = alpha_chars / total_chars if total_chars else 0
        if alpha_ratio < MIN_ALPHA_RATIO:
            return ChunkQuality(
                is_valid=False,
                reason=f"low_alpha_ratio:{alpha_ratio:.2f}",
                quality_score=alpha_ratio,
            )

        # --- word counts -------------------------------------------------
        if word_count < MIN_WORD_COUNT:
            return ChunkQuality(
                is_valid=False,
                reason=f"too_few_words:{word_count}",
                quality_score=word_count / MIN_WORD_COUNT,
            )

        if unique_words < MIN_UNIQUE_WORDS:
            return ChunkQuality(
                is_valid=False,
                reason=f"too_few_unique_words:{unique_words}",
                quality_score=unique_words / MIN_UNIQUE_WORDS,
            )

        # --- repeated-char check (e.g. ".....", "-----") -----------------
        runs = re.findall(r"(.)\1{4,}", text)   # runs of 5+ identical chars
        repeated_chars = sum(len(r) * 5 for r in runs)  # approximate
        rep_ratio = repeated_chars / total_chars if total_chars else 0
        if rep_ratio > MAX_REPEATED_CHAR_RATIO:
            return ChunkQuality(
                is_valid=False,
                reason=f"high_repeated_char_ratio:{rep_ratio:.2f}",
                quality_score=1 - rep_ratio,
            )

        # --- URL-heavy check (navigation / boilerplate) ------------------
        url_chars = sum(len(m) for m in _URL_RE.findall(text))
        url_ratio = url_chars / total_chars if total_chars else 0
        if url_ratio > MAX_URL_RATIO:
            return ChunkQuality(
                is_valid=False,
                reason=f"high_url_ratio:{url_ratio:.2f}",
                quality_score=1 - url_ratio,
            )

        # --- quality score (heuristic composite) -------------------------
        diversity = min(1.0, unique_words / max(word_count, 1))
        score = round(0.5 * alpha_ratio + 0.3 * diversity + 0.2 * min(1.0, word_count / 50), 3)

        return ChunkQuality(is_valid=True, quality_score=score)

    def filter_chunks(self, chunks: list[ChunkData]) -> tuple[list[ChunkData], list[float]]:
        """Return (valid_chunks, quality_scores) for the valid subset."""
        valid: list[ChunkData] = []
        scores: list[float] = []
        for chunk in chunks:
            result = self.validate(chunk)
            if result.is_valid:
                valid.append(chunk)
                scores.append(result.quality_score)
        return valid, scores
