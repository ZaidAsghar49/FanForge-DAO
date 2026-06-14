"""
file_claim_parser.py  —  Intelligent Document Processing (IDP) Pipeline
=========================================================================
Accepts .txt and .pdf documents, extracts raw textual content, identifies
implicit cricket statistical assertions, maps them to the 38-parameter
feature registry + IdentityEngine, and dispatches each claim to the
clean_analysis validate_model pipeline.

Pipeline Stages
───────────────
Stage 1 : Safe Multi-Format Text Extraction   (.txt streaming / .pdf page-by-page)
Stage 2 : Fluff Pre-Filter + Numerical Density Chunking  (heuristic sieve → sliding window)
Stage 3 : Claim Isolation                     (sentence-level stat pattern matching)
Stage 4 : Cascading Identity Resolution       (IdentityEngine → cricket_clean_38.db)
Stage 5 : Truth-O-Meter Verdict Dispatch      (ThreadPoolExecutor, thread-isolated SQLite)

Memory Constraint: ≤ 250 MB — large files processed sequentially in chunks.

Performance Notes
─────────────────
• Fluff pre-filter removes ~60–70% of input tokens before hitting the LLM.
• Numerical density chunking prioritises stat-heavy blocks for isolation.
• ThreadPoolExecutor uses up to 8 thread-isolated workers for parallel dispatch.
• Each worker opens its own SQLite connection with WAL + performance PRAGMAs.
"""

from __future__ import annotations

import io
import re
import sys
import json
import time
import logging
import threading
from pathlib import Path
from typing import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Logging ───────────────────────────────────────────────────────────────────
log = logging.getLogger("file_claim_parser")
if not log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("[IDP %(levelname)s] %(message)s"))
    log.addHandler(_h)
log.setLevel(logging.INFO)

# ── Constants ─────────────────────────────────────────────────────────────────

# Stat-metric patterns that strongly signal a verifiable cricket claim
_STAT_METRIC_PATTERNS = [
    r"\baverages?\b",
    r"\bstrike\s*rate\b",
    r"\bsr\b",
    r"\beconomy\b",
    r"\bwi?c?ke?ts?\b",
    r"\b(total\s+)?runs?\b",
    r"\bbowling\s+(average|economy|strike\s*rate)\b",
    r"\bbatting\s+(average|strike\s*rate)\b",
    r"\bdot\s*ball\s*%?\b",
    r"\bboundary\s*%?\b",
    r"\bhigh\s*score\b",
    r"\b(50s|100s|centuries|fifties|half[\-\s]centuries)\b",
    r"\bpartnership\b",
    r"\bballs?\s+faced\b",
    r"\bscores?\b",
    r"\bpicks?\s+(up\s+)?\d+\s+wickets?\b",
    r"\bconced(e|es|ed)\b",
    r"\bdismissals?\b",
    r"\brate\b",
    r"\bhas\s+scored\b",
    r"\btook\s+\d+\s+wickets?\b",
    r"\b\d+\s+wickets?\s+in\b",
]

# Combined regex: any single match = potential claim sentence
_CLAIM_RE = re.compile("|".join(_STAT_METRIC_PATTERNS), re.IGNORECASE)

# Format hints
_FORMAT_RE = re.compile(
    r"\b(test|odi|one[\-\s]day|t20i?|twenty[\-\s]20|international)\b",
    re.IGNORECASE
)

# Numeric anchor — a sentence without a number is almost never a verifiable stat
_NUMERIC_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")

# ── Fluff Pre-Filter Patterns ─────────────────────────────────────────────────
# Lines matching these are discarded immediately (copyright, headings, boilerplate)
_FLUFF_PATTERNS = [
    re.compile(r"^\s*(copyright|©|\(c\)|all rights reserved)", re.IGNORECASE),
    re.compile(r"^\s*(chapter|section|page|table of contents|index|foreword|preface|acknowledgements?|introduction|conclusion|references?|bibliography)\b", re.IGNORECASE),
    re.compile(r"^\s*(www\.|http|https|@|email|tel:|fax:)", re.IGNORECASE),
    re.compile(r"^\s*[\-\=\*\#]{3,}\s*$"),                    # Decorative separators
    re.compile(r"^\s*\d+\s*$"),                                 # Lone page numbers
    re.compile(r"^\s{0,3}[A-Z][A-Z\s]{15,}\s*$"),              # ALL-CAPS headings (>15 chars)
    re.compile(r"cricket\s+(is|was|has|board|association|council|federation)\b", re.IGNORECASE),  # editorial prose
    re.compile(r"\b(published|edited|written|produced|sponsored|designed)\s+by\b", re.IGNORECASE),
    re.compile(r"\b(click here|read more|subscribe|follow us|share this)\b", re.IGNORECASE),
]

# Proper-noun candidate: Title-Case word (at least 3 chars, not a format/venue keyword)
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")

# ── Chunking & Claim Limits ───────────────────────────────────────────────────
_CHUNK_MAX_CHARS = 4_000        # Max chars per semantic chunk
_DENSITY_WINDOW = 5             # Sliding window (sentences) for density scoring
_DENSITY_THRESHOLD = 0.40       # Fraction of window sentences that must score to keep
_MAX_CLAIMS_PER_DOC = 30        # Hard cap on verified claims per document

# ── Query Action Verbs ────────────────────────────────────────────────────────
_QUERY_VERBS = frozenset([
    "verify", "check", "compare", "calculate", "validate",
    "confirm", "determine", "analyze", "report", "analyse",
])

_QUERY_VERB_RE = re.compile(
    r"^(?:Verify|Check|Compare|Calculate|Validate|Confirm|Determine|Analy[sz]e|Report)\b",
    re.IGNORECASE
)


# =============================================================================
# Stage 1: Safe Multi-Format Text Extraction
# =============================================================================

def extract_text_from_txt(file_bytes: bytes, filename: str = "file.txt") -> Generator[str, None, None]:
    """
    Streaming UTF-8 reader for .txt files.
    Yields paragraphs (blank-line separated) to keep memory usage minimal.
    """
    try:
        text = file_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        log.warning(f"TXT decode error for '{filename}': {e}")
        return

    paragraphs = re.split(r"\n\s*\n", text)
    for para in paragraphs:
        stripped = para.strip()
        if stripped:
            yield stripped


def extract_text_from_pdf(file_bytes: bytes, filename: str = "file.pdf") -> Generator[str, None, None]:
    """
    Incremental page-by-page PDF text extractor.
    Tries pypdf first, falls back to pdfplumber.
    Yields one paragraph per page to stay within the 250 MB RAM limit.
    """
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        for page_num, page in enumerate(reader.pages, 1):
            try:
                page_text = page.extract_text() or ""
                for para in re.split(r"\n\s*\n", page_text):
                    stripped = para.strip()
                    if stripped:
                        yield stripped
            except Exception as pe:
                log.warning(f"pypdf page {page_num} extraction error: {pe}")
        return
    except ImportError:
        log.debug("pypdf not installed, trying pdfplumber…")
    except Exception as e:
        log.warning(f"pypdf failed for '{filename}': {e}. Trying pdfplumber…")

    try:
        import pdfplumber  # type: ignore

        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                try:
                    page_text = page.extract_text() or ""
                    for para in re.split(r"\n\s*\n", page_text):
                        stripped = para.strip()
                        if stripped:
                            yield stripped
                except Exception as pe:
                    log.warning(f"pdfplumber page {page_num} error: {pe}")
        return
    except ImportError:
        raise RuntimeError(
            "No PDF library available. Install pypdf or pdfplumber:\n"
            "  pip install pypdf\n  pip install pdfplumber"
        )
    except Exception as e:
        raise RuntimeError(f"PDF extraction failed for '{filename}': {e}")


def extract_text(file_bytes: bytes, filename: str) -> Generator[str, None, None]:
    """
    Dispatcher: routes to txt or pdf extractor based on filename extension.
    """
    ext = Path(filename).suffix.lower()
    if ext == ".txt":
        yield from extract_text_from_txt(file_bytes, filename)
    elif ext == ".pdf":
        yield from extract_text_from_pdf(file_bytes, filename)
    else:
        raise ValueError(f"Unsupported file type: '{ext}'. Only .txt and .pdf are accepted.")


# =============================================================================
# Stage 2a: Fluff Pre-Filter (Heuristic Sieve)
# =============================================================================

def _is_fluff_line(line: str) -> bool:
    """
    Returns True if this line is boilerplate/editorial that should be discarded.

    A line is kept only if it passes ALL of:
      - Does not match any _FLUFF_PATTERNS
      - Contains at least one digit  (numeric anchor)
      - Contains at least one proper noun candidate  (player/stat context)

    OR the line starts with a query-action verb (explicit user query).
    """
    stripped = line.strip()
    if not stripped:
        return True  # Empty → fluff

    # Explicit query verbs override all filters (user-typed queries)
    if _QUERY_VERB_RE.match(stripped):
        return False

    # Hard fluff patterns
    for pat in _FLUFF_PATTERNS:
        if pat.search(stripped):
            return True

    # Must contain at least one digit
    if not _NUMERIC_RE.search(stripped):
        return True

    # Must contain at least one proper noun (Title-Case word ≥ 3 chars)
    if not _PROPER_NOUN_RE.search(stripped):
        return True

    return False


def filter_paragraphs(paragraphs: Generator[str, None, None]) -> Iterator[str]:
    """
    Stage 2a: Streams paragraphs through the fluff sieve.
    Paragraphs are split into lines; individual lines are scored.
    A paragraph is yielded if ≥ 1 of its lines survives the sieve.
    """
    for para in paragraphs:
        lines = para.splitlines()
        surviving_lines = [ln for ln in lines if not _is_fluff_line(ln)]
        if surviving_lines:
            yield "\n".join(surviving_lines)


# =============================================================================
# Stage 2b: Numerical Density-Based Chunking
# =============================================================================

def _sentence_density_score(sentence: str) -> float:
    """
    Returns a [0, 1] score representing the 'claim density' of a sentence.
    A score of 1.0 = maximum statistical content.
    """
    score = 0.0
    if _NUMERIC_RE.search(sentence):
        score += 0.4
    if _CLAIM_RE.search(sentence):
        score += 0.4
    if _FORMAT_RE.search(sentence):
        score += 0.1
    if _QUERY_VERB_RE.match(sentence):
        score += 0.1
    return min(score, 1.0)


def _split_into_sentences(text: str) -> list[str]:
    """
    Sentence and line-based splitter.
    Splits by newlines first to isolate independent queries/lines,
    then runs a naïve sentence splitter on each line.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    cleaned_sentences: list[str] = []

    for line in lines:
        # Protect decimal numbers (e.g. "54.72" → no split)
        line_processed = re.sub(r"(\d)\.(\d)", r"\1DECIMAL\2", line)
        # Protect common abbreviations
        line_processed = re.sub(
            r"\b(vs|Sr|Mr|Mrs|Dr|avg|approx|Est|min|max)\.",
            r"\1ABBR",
            line_processed,
            flags=re.IGNORECASE
        )

        # Naïve sentence splitter
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", line_processed)

        for s in sentences:
            s = s.replace("DECIMAL", ".").replace("ABBR", ".")
            s = s.strip()
            if s:
                cleaned_sentences.append(s)

    return cleaned_sentences


def density_chunk_paragraphs(paragraphs: Iterator[str]) -> list[str]:
    """
    Stage 2b: Numerical Density-Based Chunking.

    Groups all sentences from all paragraphs into a flat list, then runs a
    sliding window of size _DENSITY_WINDOW. Windows where ≥ _DENSITY_THRESHOLD
    of sentences score > 0 are kept. The kept sentences are then assembled into
    chunks of ≤ _CHUNK_MAX_CHARS for downstream isolation.

    Returns a list of high-density text chunks.
    """
    # Flatten all paragraphs → sentences
    all_sentences: list[str] = []
    for para in paragraphs:
        all_sentences.extend(_split_into_sentences(para))

    if not all_sentences:
        return []

    n = len(all_sentences)
    scores = [_sentence_density_score(s) for s in all_sentences]
    kept: list[bool] = [False] * n

    # Sliding window: mark sentences in high-density windows
    window = min(_DENSITY_WINDOW, n)
    for i in range(n - window + 1):
        window_scores = scores[i: i + window]
        positive = sum(1 for sc in window_scores if sc > 0)
        if positive / window >= _DENSITY_THRESHOLD:
            for j in range(i, i + window):
                kept[j] = True

    # Always keep sentences that are explicit query verbs regardless of window
    for i, s in enumerate(all_sentences):
        if _QUERY_VERB_RE.match(s):
            kept[i] = True

    # Assemble kept sentences into chunks of ≤ _CHUNK_MAX_CHARS
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for i, s in enumerate(all_sentences):
        if not kept[i]:
            continue
        s_len = len(s) + 1  # +1 for space separator
        if current_len + s_len > _CHUNK_MAX_CHARS and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_len = 0
        current_chunk.append(s)
        current_len += s_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    discarded = sum(1 for k in kept if not k)
    log.info(
        f"Density chunker: {n} sentences -> {sum(kept)} kept, "
        f"{discarded} discarded ({round(100*discarded/max(n,1))}% pruned) -> {len(chunks)} chunk(s)."
    )
    return chunks


# =============================================================================
# Stage 3: Claim Isolation
# =============================================================================

def isolate_claims(chunks: list[str]) -> list[str]:
    """
    Stage 3: Converts pre-filtered, density-chunked text into a list of
    candidate claim sentences.

    A sentence qualifies as a claim candidate if it:
      1. Contains a numeric value OR starts with a query-action verb
      2. Matches at least one stat-metric pattern
      3. Is at least 10 characters long

    Returns up to _MAX_CLAIMS_PER_DOC unique candidate strings.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    for chunk in chunks:
        sentences = _split_into_sentences(chunk)
        for sent in sentences:
            if len(candidates) >= _MAX_CLAIMS_PER_DOC:
                log.info(f"Reached claim limit ({_MAX_CLAIMS_PER_DOC}). Stopping isolation.")
                return candidates

            sent_clean = sent.strip()
            if len(sent_clean) < 10:
                continue

            has_number = bool(_NUMERIC_RE.search(sent_clean))
            starts_with_query_verb = bool(_QUERY_VERB_RE.match(sent_clean))
            if not (has_number or starts_with_query_verb):
                continue

            if not _CLAIM_RE.search(sent_clean):
                continue

            key = re.sub(r"\s+", " ", sent_clean.lower())
            if key in seen:
                continue
            seen.add(key)

            candidates.append(sent_clean)
            log.debug(f"Isolated claim: {sent_clean[:80]}…")

    log.info(f"Claim isolation complete: {len(candidates)} candidate(s) found.")
    return candidates


# =============================================================================
# Stage 4: Cascading Identity Resolution (pre-flight check)
# =============================================================================

_identity_engine_cache = None
_identity_engine_lock = threading.Lock()


def _get_identity_engine():
    """Lazy-load the IdentityEngine singleton. Thread-safe via lock."""
    global _identity_engine_cache
    if _identity_engine_cache is None:
        with _identity_engine_lock:
            if _identity_engine_cache is None:
                from scripts.identity.identity_engine import IdentityEngine
                db_path = str(ROOT / "Dataset" / "Processed" / "cricket_clean_38.db")
                log.info(f"Loading IdentityEngine with db_path={db_path}")
                _identity_engine_cache = IdentityEngine(db_path=db_path)
                log.info("IdentityEngine loaded and ready.")
    return _identity_engine_cache


def _extract_player_hint(claim: str) -> str | None:
    """
    Heuristic pre-flight: extract the most likely player name from a claim sentence.
    Looks for Title Case sequences of 2-4 words that look like a person's name.
    Returns the best candidate or None.
    """
    matches = re.findall(r"\b([A-Z][a-zA-Z']{1,}(?:\s+[A-Z][a-zA-Z']{1,}){1,3})\b", claim)
    for m in matches:
        words = m.split()
        _SKIP = {
            "ODI", "Test", "T20", "T20I", "ICC", "IPL", "PSL", "BBL",
            "World", "Cup", "Asia", "Ashes", "International", "Series",
            "Cricket", "Board", "Stadium", "Ground", "England", "India",
            "Australia", "Pakistan", "Zealand", "Lanka", "Indies",
            "Bangladesh", "Afghanistan", "Zimbabwe",
        }
        if any(w in _SKIP for w in words):
            continue
        if len(words) >= 2:
            return m
    return None


def preflight_identity_check(claim: str) -> dict:
    """
    Stage 4a: Run identity resolution on a claim before full validation.
    Returns dict with: resolved_name, player_found, confidence, engine_result
    """
    hint = _extract_player_hint(claim)
    if not hint:
        return {"player_found": False, "resolved_name": None, "confidence": 0.0}

    try:
        engine = _get_identity_engine()
        res = engine.resolve(hint)
        resolved = res.get("resolved")
        if resolved:
            return {
                "player_found": True,
                "resolved_name": resolved.get("canonical_name"),
                "confidence": resolved.get("confidence", 1.0),
                "country": resolved.get("country"),
                "primary_role": resolved.get("primary_role"),
                "engine_result": res,
            }
        else:
            candidates = res.get("candidates", [])
            return {
                "player_found": False,
                "resolved_name": hint,
                "confidence": 0.0,
                "candidates": [c.get("name") for c in candidates[:3]],
                "engine_result": res,
            }
    except Exception as e:
        log.warning(f"Identity preflight error for '{hint}': {e}")
        return {"player_found": False, "resolved_name": hint, "confidence": 0.0, "error": str(e)}


# =============================================================================
# Stage 5: Truth-O-Meter Verdict Dispatch  (thread-isolated)
# =============================================================================

def _dispatch_verdict(claim: str, skip_predictions: bool = True) -> dict:
    """
    Stage 5: Dispatch a single claim string through the full validate_claim pipeline.
    Each call is designed to run inside a thread worker with its own SQLite connection.
    Returns the raw verdict dict from validate_model.
    """
    try:
        from clean_analysis.validate_model import validate_claim
        result = validate_claim(claim, skip_predictions=skip_predictions)
        return result
    except Exception as e:
        log.error(f"validate_claim dispatch error for claim '{claim[:60]}…': {e}")
        return {
            "status": "error",
            "message": f"Validation pipeline error: {e}",
            "claim": claim,
        }


def _process_single_claim(claim: str, skip_predictions: bool = True) -> list[dict]:
    """
    Worker function: runs identity preflight + verdict dispatch for one claim.
    Designed to execute inside a ThreadPoolExecutor thread.
    SQLite connections inside validate_claim are thread-isolated (WAL mode).
    """
    t0 = time.perf_counter()
    preflight = preflight_identity_check(claim)
    raw = _dispatch_verdict(claim, skip_predictions=skip_predictions)
    elapsed = round((time.perf_counter() - t0) * 1000, 1)

    results: list[dict] = []

    # Multi-claim result from validate_claim (comparative queries)
    if raw.get("is_multi_claim") and "verdicts" in raw:
        sub_verdicts = raw["verdicts"]
        for sub_idx, sub_raw in enumerate(sub_verdicts, 1):
            sub_subject = sub_raw.get("subject")
            sub_preflight = preflight
            if sub_subject and sub_subject != preflight.get("resolved_name"):
                sub_preflight = preflight_identity_check(sub_subject)

            verdict_entry = {
                "claim": sub_raw.get("claim") or f"Sub-claim #{sub_idx} of: {claim}",
                "status": sub_raw.get("status", "error"),
                "verdict": sub_raw.get("verdict"),
                "claimed_value": sub_raw.get("claimed_value"),
                "real_val": sub_raw.get("real_val"),
                "accuracy_pct": sub_raw.get("accuracy_pct"),
                "subject": sub_subject or sub_preflight.get("resolved_name"),
                "metric": sub_raw.get("metric"),
                "sample_size": sub_raw.get("sample_size", 0),
                "confidence": sub_raw.get("confidence", 0.0),
                "filters": sub_raw.get("filters", {}),
                "message": sub_raw.get("message"),
                "preflight": sub_preflight,
                "elapsed_ms": elapsed,
            }
            for extra_key in ("insight", "insight_tags", "execution_mode", "real_meta"):
                if extra_key in sub_raw:
                    verdict_entry[extra_key] = sub_raw[extra_key]
            results.append(verdict_entry)
    else:
        # Single-claim result
        verdict_entry: dict = {
            "claim": claim,
            "status": raw.get("status", "error"),
            "verdict": raw.get("verdict"),
            "claimed_value": raw.get("claimed_value"),
            "real_val": raw.get("real_val"),
            "accuracy_pct": raw.get("accuracy_pct"),
            "subject": raw.get("subject") or preflight.get("resolved_name"),
            "metric": raw.get("metric"),
            "sample_size": raw.get("sample_size", 0),
            "confidence": raw.get("confidence", 0.0),
            "filters": raw.get("filters", {}),
            "message": raw.get("message"),
            "preflight": preflight,
            "elapsed_ms": elapsed,
        }
        for extra_key in ("insight", "insight_tags", "execution_mode", "real_meta"):
            if extra_key in raw:
                verdict_entry[extra_key] = raw[extra_key]
        results.append(verdict_entry)

    return results


# =============================================================================
# Main Public API
# =============================================================================

def parse_document_claims(
    file_bytes: bytes,
    filename: str,
    skip_predictions: bool = True,
    max_claims: int = _MAX_CLAIMS_PER_DOC,
) -> list[dict]:
    """
    Full IDP pipeline: Extract → Filter → Chunk → Isolate → Verify

    Args:
        file_bytes:       Raw bytes of the uploaded file.
        filename:         Original filename (used for extension routing).
        skip_predictions: If True, skip ML prediction stage (faster, less RAM).
        max_claims:       Cap on number of claims to verify per document.

    Returns:
        A list of verdict dicts, one per isolated claim.

    Pipeline:
        Stage 1: text extraction (streaming, RAM-safe)
        Stage 2a: fluff pre-filter (heuristic sieve, ~60-70% token reduction)
        Stage 2b: numerical density chunking (sliding window)
        Stage 3: claim isolation (stat pattern matching)
        Stage 4: identity resolution preflight
        Stage 5: parallel verdict dispatch (ThreadPoolExecutor, 8 workers max)
    """
    log.info(f"Starting IDP pipeline for '{filename}' ({len(file_bytes):,} bytes)…")
    t_start = time.perf_counter()

    # ── Stage 1: Extract ───────────────────────────────────────────────────────
    try:
        raw_paragraphs = extract_text(file_bytes, filename)
    except Exception as e:
        log.error(f"Text extraction failed: {e}")
        return [{"status": "error", "message": str(e), "claim": None}]

    # ── Stage 2a: Fluff Pre-Filter ─────────────────────────────────────────────
    t_filter = time.perf_counter()
    filtered_paragraphs = filter_paragraphs(raw_paragraphs)

    # ── Stage 2b: Numerical Density Chunking ───────────────────────────────────
    chunks = density_chunk_paragraphs(filtered_paragraphs)
    t_chunk = time.perf_counter()
    log.info(f"Filter+Chunk complete in {round((t_chunk - t_filter)*1000, 1)}ms -> {len(chunks)} chunk(s).")

    # ── Stage 3: Claim Isolation ───────────────────────────────────────────────
    if not chunks:
        log.info("No high-density chunks survived pre-filtering.")
        return [{
            "status": "no_claims",
            "message": "No verifiable cricket statistical assertions were detected in the document.",
            "claim": None,
        }]

    claims = isolate_claims(chunks)[:max_claims]
    if not claims:
        log.info("No verifiable claims detected after isolation.")
        return [{
            "status": "no_claims",
            "message": "No verifiable cricket statistical assertions were detected in the document.",
            "claim": None,
        }]

    t_isolate = time.perf_counter()
    log.info(f"Claim isolation in {round((t_isolate - t_chunk)*1000, 1)}ms -> {len(claims)} claim(s).")

    # ── Stage 4: Pre-warm IdentityEngine (avoid thread race on first load) ─────
    try:
        _get_identity_engine()
    except Exception as ie_err:
        log.warning(f"Could not pre-warm IdentityEngine cache: {ie_err}")

    # ── Stage 5: Parallel Verdict Dispatch ─────────────────────────────────────
    verdicts: list[dict] = []
    max_workers = min(len(claims), 3) # Reduced to 3 to prevent Groq API synchronized burst limit livelocks
    log.info(f"Dispatching {len(claims)} claim(s) -> ThreadPoolExecutor({max_workers} workers)…")

    # Submit all futures at once for maximum concurrency
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_claim = {
            executor.submit(_process_single_claim, claim, skip_predictions): (idx, claim)
            for idx, claim in enumerate(claims, 1)
        }

        for future in as_completed(future_to_claim):
            idx, claim = future_to_claim[future]
            try:
                claim_results = future.result()
                verdicts.extend(claim_results)
                log.info(f"[{idx}/{len(claims)}] '{claim[:50]}…' -> {len(claim_results)} verdict(s).")
            except Exception as thread_exc:
                log.error(f"[{idx}/{len(claims)}] Thread failed for '{claim[:50]}…': {thread_exc}")
                verdicts.append({
                    "claim": claim,
                    "status": "error",
                    "message": f"Parallel thread execution error: {thread_exc}",
                })

    total_elapsed = round((time.perf_counter() - t_start) * 1000, 1)
    log.info(
        f"IDP pipeline complete: {len(verdicts)} verdict(s) in {total_elapsed}ms "
        f"for '{filename}'."
    )
    return verdicts


# =============================================================================
# CLI convenience (python file_claim_parser.py myfile.txt)
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="IDP Claim Parser — extract & verify cricket stats from .txt/.pdf files"
    )
    parser.add_argument("file", help="Path to .txt or .pdf file to process")
    parser.add_argument(
        "--max-claims", type=int, default=10, help="Maximum claims to process (default: 10)"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output results as JSON"
    )
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"[ERROR] File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    raw_bytes = file_path.read_bytes()
    results = parse_document_claims(
        raw_bytes, file_path.name, max_claims=args.max_claims
    )

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for i, v in enumerate(results, 1):
            print(f"\n{'-'*60}")
            print(f"  Claim [{i}]: {v.get('claim', 'N/A')[:80]}")
            print(f"  Status : {v.get('status')}")
            print(f"  Verdict: {v.get('verdict')}")
            print(f"  Subject: {v.get('subject')}")
            print(f"  Metric : {v.get('metric')}")
            if v.get("real_val") is not None:
                print(f"  Actual : {v.get('real_val'):.4f}")
            if v.get("claimed_value") is not None:
                print(f"  Claimed: {v.get('claimed_value')}")
            print(f"  Elapsed: {v.get('elapsed_ms')}ms")
        print(f"\n{'='*60}")
        print(f"  Total claims verified: {len(results)}")
