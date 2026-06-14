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
Stage 2 : Semantic Chunking & Claim Isolation  (paragraph → sentence → claim segmenter)
Stage 3 : Cascading Identity Resolution        (IdentityEngine → cricket_clean_38.db)
Stage 4 : Truth-O-Meter Verdict Dispatch       (validate_claim per isolated claim)

Memory Constraint: ≤ 250 MB — large files processed sequentially in chunks.
"""

from __future__ import annotations

import io
import re
import sys
import json
import time
import logging
from pathlib import Path
from typing import Generator

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
# Patterns that strongly signal a cricket claim exists in a sentence
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

# Combined regex (any single match = potential claim sentence)
_CLAIM_RE = re.compile("|".join(_STAT_METRIC_PATTERNS), re.IGNORECASE)

# Format hints
_FORMAT_RE = re.compile(
    r"\b(test|odi|one[\-\s]day|t20i?|twenty[\-\s]20|international)\b",
    re.IGNORECASE
)

# Numeric anchor — a sentence without a number is almost never a verifiable stat
_NUMERIC_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")

# Max characters per chunk we pass to the LLM parser (RAM guard)
_CHUNK_MAX_CHARS = 4_000
# Max distinct claims we attempt to verify per document (RAM guard)
_MAX_CLAIMS_PER_DOC = 30


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

    # Split into paragraphs (blank-line separated)
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
    # Attempt pypdf (lightweight, fast)
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

    # Fallback: pdfplumber (more accurate layout extraction)
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
# Stage 2: Semantic Chunking & Claim Isolation
# =============================================================================

def _split_into_sentences(text: str) -> list[str]:
    """
    Sentence and line-based splitter. Splits by newlines first to isolate independent
    queries (protecting middle-of-sentence wraps), then runs a naïve sentence splitter on each line.
    """
    query_verbs = r"(?:Verify|Check|Compare|Calculate|Validate|Confirm|Determine|Analyze|Report)"
    
    # 1. Replace newlines followed by a query verb with a placeholder
    text_processed = re.sub(rf"\s*[\r\n]+\s*(?={query_verbs}\b)", "__QUERY_SEP__", text)
    # 2. Replace other newlines (middle-of-sentence line wraps) with a space
    text_processed = re.sub(r"\s*[\r\n]+\s*", " ", text_processed)
    # 3. Restore placeholder to clean newlines
    text_processed = text_processed.replace("__QUERY_SEP__", "\n")
    
    lines = text_processed.split("\n")
    cleaned_sentences = []
    
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
            
        # Protect decimal numbers from being split (e.g. "54.72" -> no split)
        line_processed = re.sub(r"(\d)\.(\d)", r"\1DECIMAL\2", line_stripped)
        # Protect common abbreviations
        line_processed = re.sub(r"\b(vs|Sr|Mr|Mrs|Dr|avg|approx|Est|min|max)\.", r"\1ABBR", line_processed, flags=re.IGNORECASE)

        # Naïve sentence splitter on the line
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", line_processed)

        # Restore decimals/abbreviations and clean up
        for s in sentences:
            s = s.replace("DECIMAL", ".").replace("ABBR", ".")
            s = s.strip()
            if s:
                cleaned_sentences.append(s)
                
    return cleaned_sentences


def isolate_claims(paragraphs: Generator[str, None, None]) -> list[str]:
    """
    Stage 2: Converts a stream of paragraphs into a list of candidate claim sentences.

    A sentence is considered a claim candidate if it:
      1. Contains a numeric value OR starts with a query-starting action verb
      2. Matches at least one stat-metric pattern
      3. Is at least 10 characters long

    Returns up to _MAX_CLAIMS_PER_DOC unique candidate strings.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    for para in paragraphs:
        sentences = _split_into_sentences(para)
        for sent in sentences:
            if len(candidates) >= _MAX_CLAIMS_PER_DOC:
                log.info(f"Reached claim limit ({_MAX_CLAIMS_PER_DOC}). Stopping isolation.")
                return candidates

            sent_clean = sent.strip()
            if len(sent_clean) < 10:
                continue

            # Must have a numeric anchor OR start with a query verb
            has_number = bool(_NUMERIC_RE.search(sent_clean))
            starts_with_query_verb = any(sent_clean.lower().startswith(v) for v in [
                "verify", "check", "compare", "calculate", "validate", "confirm", "determine", "analyze", "report"
            ])
            if not (has_number or starts_with_query_verb):
                continue

            # Must match a stat-metric pattern
            if not _CLAIM_RE.search(sent_clean):
                continue

            # Deduplicate
            key = re.sub(r"\s+", " ", sent_clean.lower())
            if key in seen:
                continue
            seen.add(key)

            candidates.append(sent_clean)
            log.debug(f"Isolated claim: {sent_clean[:80]}…")

    log.info(f"Claim isolation complete: {len(candidates)} candidate(s) found.")
    return candidates


# =============================================================================
# Stage 3: Cascading Identity Resolution (pre-flight check)
# =============================================================================

_identity_engine_cache = None


def _get_identity_engine():
    """Lazy-load the IdentityEngine singleton for the clean DB."""
    global _identity_engine_cache
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
    # Pattern: 2–4 consecutive Title-Case words (at least one with 3+ chars)
    matches = re.findall(r"\b([A-Z][a-zA-Z']{1,}(?:\s+[A-Z][a-zA-Z']{1,}){1,3})\b", claim)
    for m in matches:
        words = m.split()
        # Filter out obvious non-name proper nouns (format names, venues, etc.)
        _SKIP = {"ODI", "Test", "T20", "T20I", "ICC", "IPL", "PSL", "BBL",
                 "World", "Cup", "Asia", "Ashes", "International", "Series",
                 "Cricket", "Board", "Stadium", "Ground", "England", "India",
                 "Australia", "Pakistan", "Zealand", "Lanka", "Indies",
                 "Bangladesh", "Afghanistan", "Zimbabwe"}
        if any(w in _SKIP for w in words):
            continue
        if len(words) >= 2:
            return m
    return None


def preflight_identity_check(claim: str) -> dict:
    """
    Stage 3: Run identity resolution on a claim before full validation.
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
                "resolved_name": hint,  # raw hint
                "confidence": 0.0,
                "candidates": [c.get("name") for c in candidates[:3]],
                "engine_result": res,
            }
    except Exception as e:
        log.warning(f"Identity preflight error for '{hint}': {e}")
        return {"player_found": False, "resolved_name": hint, "confidence": 0.0, "error": str(e)}


# =============================================================================
# Stage 4: Truth-O-Meter Verdict Dispatch
# =============================================================================

def _dispatch_verdict(claim: str, skip_predictions: bool = True) -> dict:
    """
    Stage 4: Dispatch a single claim string through the full validate_claim pipeline.
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


# =============================================================================
# Main Public API
# =============================================================================

def _process_single_claim(claim: str, skip_predictions: bool = True) -> list[dict]:
    """Helper to process a single claim for parallel threading."""
    t0 = time.perf_counter()
    preflight = preflight_identity_check(claim)
    raw = _dispatch_verdict(claim, skip_predictions=skip_predictions)
    elapsed = round((time.perf_counter() - t0) * 1000, 1)

    results = []
    # Check if it's a multi-claim result from validate_claim refactor
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
        # Normalize single-claim output
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


def parse_document_claims(
    file_bytes: bytes,
    filename: str,
    skip_predictions: bool = True,
    max_claims: int = _MAX_CLAIMS_PER_DOC,
) -> list[dict]:
    """
    Full IDP pipeline: Extract → Chunk → Isolate → Verify

    Args:
        file_bytes:       Raw bytes of the uploaded file.
        filename:         Original filename (used for extension routing).
        skip_predictions: If True, skip ML prediction stage (faster, less RAM).
        max_claims:       Cap on number of claims to verify per document.

    Returns:
        A list of verdict dicts, one per isolated claim.
    """
    log.info(f"Starting IDP pipeline for '{filename}' ({len(file_bytes):,} bytes)…")
    t_start = time.perf_counter()

    # Stage 1: Extract
    try:
        paragraphs = extract_text(file_bytes, filename)
    except Exception as e:
        log.error(f"Text extraction failed: {e}")
        return [{"status": "error", "message": str(e), "claim": None}]

    # Stage 2: Isolate claims (RAM-safe generator pipeline)
    claims = isolate_claims(paragraphs)[:max_claims]
    if not claims:
        log.info("No verifiable claims detected in document.")
        return [{
            "status": "no_claims",
            "message": "No verifiable cricket statistical assertions were detected in the document.",
            "claim": None,
        }]

    # Pre-warm IdentityEngine cache to avoid thread race conditions
    try:
        _get_identity_engine()
    except Exception as ie_err:
        log.warning(f"Could not pre-warm IdentityEngine cache: {ie_err}")

    # Stage 3 & 4: Identity preflight + Verdict dispatch in parallel
    from concurrent.futures import ThreadPoolExecutor

    verdicts: list[dict] = []
    # Dynamic workers count based on claims (cap at 8 to avoid overwhelming API limits)
    max_workers = min(len(claims), 8)
    log.info(f"Dispatching {len(claims)} claims to ThreadPoolExecutor with {max_workers} workers…")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_process_single_claim, claim, skip_predictions)
            for claim in claims
        ]
        for idx, fut in enumerate(futures, 1):
            try:
                claim_results = fut.result()
                verdicts.extend(claim_results)
                log.info(f"[{idx}/{len(claims)}] Processed successfully.")
            except Exception as thread_exc:
                log.error(f"[{idx}/{len(claims)}] Thread execution failed: {thread_exc}")
                verdicts.append({
                    "claim": claims[idx - 1] if idx - 1 < len(claims) else "Unknown",
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
            print(f"\n{'─'*60}")
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
