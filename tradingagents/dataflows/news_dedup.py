"""News deduplication and near-duplicate clustering.

Operates on a vendor-neutral list of dicts. Each input article must expose:

- ``title``: str
- ``publisher``: str | None  (used as the source-rank tiebreaker)
- ``pub_date``: datetime | None  (used as the secondary tiebreaker)

Other keys are passed through unchanged. The pipeline has two passes:

1. Exact dedup on a *normalized* title (case-folded, punctuation stripped,
   whitespace collapsed). This is cheap and removes copy-paste reposts.

2. Token-set Jaccard clustering at threshold 0.7. Two titles with mostly
   the same content words land in the same cluster regardless of word
   order or filler punctuation. Within a cluster, the highest-source-rank
   article wins; ties are broken by the most recent ``pub_date``.

Designed so the LLM never sees the same Reuters story repackaged by 30
outlets. ``cluster_articles`` is the single entry point both vendor news
modules call.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable

from .source_rank import get_source_rank

# ---------------------------------------------------------------------------
# Title normalization
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# Stopwords stripped before token-set comparison so syndication boilerplate
# ("the", "a", "for") doesn't dominate Jaccard scores on short titles.
_STOPWORDS = frozenset(
    {
        "a", "an", "the",
        "and", "or", "but",
        "for", "to", "of", "in", "on", "at", "by", "from", "with", "as",
        "is", "are", "was", "were", "be", "been", "being",
        "this", "that", "these", "those",
        "it", "its",
        "s",  # genitive remnant after punctuation strip
    }
)

# Default Jaccard threshold — tuned so syndicated copies of the same wire
# story collapse, but distinct earnings/news items on the same ticker stay
# separate. 0.6 is loose enough that minor rewordings ("driven by AI demand"
# vs "AI demand the driver") still cluster, but distinct stories on the
# same ticker (an earnings beat vs a recall) reliably stay split.
DEFAULT_JACCARD_THRESHOLD = 0.6


def normalize_title(title: str | None) -> str:
    """Return a lowercase, punctuation-free, whitespace-collapsed title.

    Empty / None titles return ``""`` so downstream code can use truthiness
    as a "skip this article" signal.
    """
    if not title:
        return ""
    cleaned = _PUNCT_RE.sub(" ", title.lower())
    return _WS_RE.sub(" ", cleaned).strip()


def _title_tokens(title: str | None) -> frozenset[str]:
    """Stopword-stripped token set used for Jaccard similarity."""
    norm = normalize_title(title)
    if not norm:
        return frozenset()
    return frozenset(t for t in norm.split() if t and t not in _STOPWORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


# ---------------------------------------------------------------------------
# Tiebreakers
# ---------------------------------------------------------------------------


def _exemplar_key(article: dict[str, Any]) -> tuple[int, float]:
    """Sort key for picking the best exemplar within a cluster.

    Higher source rank wins; on ties the more recent ``pub_date`` wins.
    Articles without ``pub_date`` get ``-inf`` so they lose recency ties.
    """
    rank = get_source_rank(article.get("publisher"))
    pd = article.get("pub_date")
    if isinstance(pd, datetime):
        recency = pd.timestamp()
    else:
        recency = float("-inf")
    return (rank, recency)


# ---------------------------------------------------------------------------
# Public pipeline
# ---------------------------------------------------------------------------


def dedup_exact(articles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop articles whose normalized title was already seen.

    First-seen-wins. Articles with empty/missing titles are dropped — they
    carry no signal the LLM can use, and keeping them defeats clustering.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for art in articles:
        norm = normalize_title(art.get("title"))
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(art)
    return out


def cluster_articles(
    articles: Iterable[dict[str, Any]],
    *,
    jaccard_threshold: float = DEFAULT_JACCARD_THRESHOLD,
) -> list[dict[str, Any]]:
    """Token-set Jaccard clustering with rank-aware exemplar selection.

    Returns one exemplar per cluster, ordered by descending source rank
    then descending ``pub_date``. Stable for inputs that happen to share
    rank and date.

    Exact-title duplicates fall out as a Jaccard-1.0 special case, so we
    don't need a separate exact-dedup pre-pass — and skipping that pass
    means the rank/recency tiebreaker applies even to byte-identical titles
    (the prior first-seen-wins pre-pass leaked the worse-source copy through).

    Articles with no usable title tokens are dropped.

    Complexity is O(n*k) where k is the number of clusters formed — fine
    for the article counts vendors return (≤ ~50). For thousands of
    articles, replace with a locality-sensitive hash.
    """
    clusters: list[list[dict[str, Any]]] = []
    cluster_tokens: list[frozenset[str]] = []

    for art in articles:
        toks = _title_tokens(art.get("title"))
        if not toks:
            continue

        placed = False
        for idx, ref_toks in enumerate(cluster_tokens):
            if _jaccard(toks, ref_toks) >= jaccard_threshold:
                clusters[idx].append(art)
                placed = True
                break
        if not placed:
            clusters.append([art])
            cluster_tokens.append(toks)

    if not clusters:
        return []

    exemplars = [max(group, key=_exemplar_key) for group in clusters]
    exemplars.sort(key=_exemplar_key, reverse=True)
    return exemplars
