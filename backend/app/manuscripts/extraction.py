"""Text extraction and chapter detection for manuscripts.

Per DECISION_003 JUDGE amendments:
- No bare-number regex for chapter detection.
- Minimum 200 words per chapter (merge short sections with next).
- Cap at 150 chapters; fall back to single chapter if exceeded.

Per DECISION_007:
- LLM-assisted structure detection for any manuscript format.
- Fallback chain: LLM -> auto-split -> regex.
- Supports novels, plays, poetry, essays, screenplays, etc.
"""

from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path

from docx import Document as DocxDocument
from PyPDF2 import PdfReader

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gutenberg preamble / license stripping
# ---------------------------------------------------------------------------

# Matches the start-of-text marker in Project Gutenberg files
_GUTENBERG_START = re.compile(
    r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*",
    re.IGNORECASE,
)
# Matches the end-of-text marker
_GUTENBERG_END = re.compile(
    r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*",
    re.IGNORECASE,
)
# Fallback: detect Gutenberg header by title line
_GUTENBERG_HEADER = re.compile(
    r"^.*Project Gutenberg.*eBook", re.IGNORECASE | re.MULTILINE
)


def _strip_gutenberg(text: str) -> str:
    """Strip Project Gutenberg preamble and license text if present."""
    # Try the *** START/END markers first (most reliable)
    start_match = _GUTENBERG_START.search(text)
    end_match = _GUTENBERG_END.search(text)

    if start_match and end_match and start_match.end() < end_match.start():
        stripped = text[start_match.end():end_match.start()].strip()
        logger.info(
            f"Stripped Gutenberg preamble/license via *** markers "
            f"({len(text) - len(stripped)} chars removed)"
        )
        return stripped

    # Fallback: if we see "Project Gutenberg eBook" in the first 2000 chars,
    # look for the first blank-line-separated block after it as the start of content
    if _GUTENBERG_HEADER.search(text[:2000]):
        # Find the first chapter-like header or significant text block
        # Skip everything before the first double newline after the header
        lines = text.split("\n")
        in_preamble = True
        blank_count = 0
        content_start = 0
        for i, line in enumerate(lines):
            stripped_line = line.strip()
            if in_preamble:
                if not stripped_line:
                    blank_count += 1
                else:
                    blank_count = 0
                # After seeing the header, wait for a substantial gap (3+ blank lines)
                # which typically separates the preamble from the actual text
                if blank_count >= 3 and i > 10:
                    content_start = sum(len(l) + 1 for l in lines[:i])
                    in_preamble = False
        if not in_preamble:
            stripped = text[content_start:].strip()
            logger.info(
                f"Stripped Gutenberg preamble via blank-line heuristic "
                f"({content_start} chars removed from start)"
            )
            # Also try to strip the license at the end
            # Look for common Gutenberg end markers
            end_markers = [
                "End of the Project Gutenberg",
                "End of Project Gutenberg",
                "*** END OF THE PROJECT",
                "*** END OF THIS PROJECT",
            ]
            for marker in end_markers:
                idx = stripped.lower().rfind(marker.lower())
                if idx > len(stripped) // 2:  # Only if in the latter half
                    stripped = stripped[:idx].strip()
                    logger.info(f"Stripped Gutenberg license from end")
                    break
            return stripped

    return text


# ---------------------------------------------------------------------------
# Chapter header patterns
# ---------------------------------------------------------------------------

CHAPTER_WORD_NUMBERS = (
    "One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten|"
    "Eleven|Twelve|Thirteen|Fourteen|Fifteen|Sixteen|Seventeen|Eighteen|Nineteen|Twenty|"
    "Twenty-?One|Twenty-?Two|Twenty-?Three|Twenty-?Four|Twenty-?Five|"
    "Twenty-?Six|Twenty-?Seven|Twenty-?Eight|Twenty-?Nine|Thirty|"
    "Thirty-?One|Thirty-?Two|Thirty-?Three|Thirty-?Four|Thirty-?Five|"
    "Forty|Fifty|Sixty|Seventy|Eighty|Ninety|Hundred"
)

ROMAN_NUMERAL = r"[IVXLC]+"

# Each pattern returns the full matched header text as group(0) or group(1)
CHAPTER_PATTERNS = [
    # "Chapter 1", "CHAPTER 12", "chapter 3" — optionally followed by subtitle on same line
    re.compile(r"^(Chapter\s+\d+[^\S\n]*[^\n]*)", re.IGNORECASE | re.MULTILINE),
    # "Chapter One", "Chapter Twenty-Three"
    re.compile(
        rf"^(Chapter\s+(?:{CHAPTER_WORD_NUMBERS})\b[^\S\n]*[^\n]*)",
        re.IGNORECASE | re.MULTILINE,
    ),
    # "CHAPTER I.", "CHAPTER XIV", "CHAPTER I. Down the Rabbit-Hole"
    # All-caps CHAPTER followed by Roman numeral (with optional period and subtitle)
    re.compile(
        rf"^(CHAPTER\s+{ROMAN_NUMERAL}\.?[^\S\n]*[^\n]*)",
        re.MULTILINE,
    ),
    # Standalone Roman numerals on their own line (preceded by blank line)
    # Handles both \n and \r\n line endings (Gutenberg style)
    re.compile(rf"(?:^|\r?\n)\s*\r?\n\s*({ROMAN_NUMERAL})\s*\r?\n", re.MULTILINE),
]

# Valid Roman numerals for chapter headers (I through L = 1-50)
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def _is_valid_roman(s: str) -> bool:
    """Check if a string is a valid Roman numeral between 1 and 50."""
    s = s.strip().upper()
    if not s or not all(c in _ROMAN_VALUES for c in s):
        return False
    total = 0
    prev = 0
    for c in reversed(s):
        val = _ROMAN_VALUES[c]
        if val < prev:
            total -= val
        else:
            total += val
        prev = val
    return 1 <= total <= 50


MIN_CHAPTER_WORDS = 200
TOC_THRESHOLD_WORDS = 50
MAX_CHAPTERS = 150
MAX_WORD_COUNT = 120_000


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text_from_docx(content: bytes) -> str:
    doc = DocxDocument(io.BytesIO(content))
    paragraphs = [p.text for p in doc.paragraphs]
    return "\n\n".join(paragraphs)


def extract_text_from_pdf(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    pages = []
    total_chars = 0

    for page in reader.pages:
        text = page.extract_text() or ""
        pages.append(text)
        total_chars += len(text)

    if len(reader.pages) > 0 and total_chars / len(reader.pages) < 100:
        raise ExtractionError(
            "This PDF appears to be a scanned image. "
            "Please export your manuscript as DOCX from your word processor."
        )

    return "\n\n".join(pages)


def extract_text_from_txt(content: bytes) -> str:
    return content.decode("utf-8")


MIN_EXTRACTED_WORDS = 50
LANGUAGE_SAMPLE_SIZE = 5000


def detect_language(text: str) -> str | None:
    """Detect the language of text. Returns ISO 639-1 code or None on failure."""
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0
        sample = text[:LANGUAGE_SAMPLE_SIZE]
        return detect(sample)
    except Exception:
        return None


def extract_text(content: bytes, ext: str) -> str:
    """Extract text from a file. Raises ExtractionError on failure or empty result."""
    if ext == ".docx":
        text = _safe_extract_docx(content)
    elif ext == ".pdf":
        text = _safe_extract_pdf(content)
    elif ext == ".txt":
        text = extract_text_from_txt(content)
    else:
        raise ExtractionError(f"Unsupported file type: {ext}")

    text = text.strip()
    if not text:
        raise ExtractionError(
            "No text could be extracted from this file. "
            "Please check that your file contains text (not just images or formatting)."
        )
    word_count = len(text.split())
    if word_count < MIN_EXTRACTED_WORDS:
        raise ExtractionError(
            f"Only {word_count} words extracted — the file appears to be nearly empty. "
            "Please upload a file with at least a few paragraphs of text."
        )

    lang = detect_language(text)
    if lang is not None and lang != "en":
        logger.info(f"Non-English manuscript detected: language={lang}")
        raise ExtractionError(
            "GhostEditor currently supports English-language manuscripts only. "
            f"This text was detected as '{lang}'."
        )

    # Strip Gutenberg preamble/license if present
    text = _strip_gutenberg(text)

    return text


def _safe_extract_docx(content: bytes) -> str:
    """Extract text from DOCX with error handling for corrupt files."""
    try:
        return extract_text_from_docx(content)
    except Exception as e:
        err_str = str(e).lower()
        if "zip" in err_str or "xml" in err_str or "parse" in err_str or "corrupt" in err_str:
            raise ExtractionError(
                "This .docx file appears to be corrupt or not a valid Word document. "
                "Try re-saving it from your word processor and uploading again."
            )
        raise ExtractionError(f"Could not read this .docx file: {e}")


def _safe_extract_pdf(content: bytes) -> str:
    """Extract text from PDF with error handling for damaged files."""
    try:
        return extract_text_from_pdf(content)
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(
            "Could not read this PDF file. It may be password-protected or damaged. "
            "Try exporting as a new PDF from your word processor."
        )


# ---------------------------------------------------------------------------
# Chapter detection
# ---------------------------------------------------------------------------

def _extract_title(text: str, pos: int, matched_header: str) -> str:
    """Extract a clean chapter title from the matched header text.

    For headers like "CHAPTER I. Down the Rabbit-Hole", returns the full line.
    Also checks the next line for a subtitle (common in Gutenberg formatting
    where the subtitle is on a separate line).
    """
    # The matched header is already the full line — clean it up
    title = matched_header.strip().rstrip(".")

    # Check if the next line is a subtitle (non-empty, not a chapter header, not too long)
    end_of_match = pos + len(matched_header)
    rest = text[end_of_match:]

    # Find next non-blank line
    lines = rest.split("\n", 3)
    for line in lines[:2]:  # Check next 1-2 lines
        stripped = line.strip()
        if not stripped:
            continue
        # If it looks like a subtitle (short, doesn't start with common text patterns)
        if len(stripped) < 80 and not stripped[0].islower():
            title = title + " — " + stripped.rstrip(".")
        break

    return title


def _detect_chapters_regex(text: str) -> list[dict]:
    """Regex-based chapter detection (fallback). Returns list of {chapter_number, title, text, word_count}.

    Per JUDGE: merge chapters < 200 words with next; cap at 150; no bare-number regex.
    """
    split_positions = []

    for i, pattern in enumerate(CHAPTER_PATTERNS):
        for match in pattern.finditer(text):
            if i == 3:
                # Roman numeral pattern uses a capture group
                numeral = match.group(1).strip()
                if not _is_valid_roman(numeral):
                    continue
                pos = match.start(1)
                split_positions.append((pos, numeral))
            else:
                # Other patterns: group(1) has the full header line
                header = match.group(1) if match.lastindex else match.group(0)
                pos = match.start(1) if match.lastindex else match.start()
                title = header.strip()
                split_positions.append((pos, title))

    if not split_positions:
        word_count = len(text.split())
        logger.info("No chapter headers detected; treating entire text as Chapter 1")
        return [{"chapter_number": 1, "title": None, "text": text, "word_count": word_count}]

    # Sort by position, deduplicate overlapping matches
    split_positions.sort(key=lambda x: x[0])
    deduped = [split_positions[0]]
    for pos, title in split_positions[1:]:
        if pos - deduped[-1][0] > 50:
            deduped.append((pos, title))
        else:
            # Keep the longer/more descriptive title for overlapping matches
            if len(title) > len(deduped[-1][1]):
                deduped[-1] = (deduped[-1][0], title)
    split_positions = deduped

    # --- TOC FILTER ---
    filtered_positions = []
    for i, (pos, title) in enumerate(split_positions):
        end = split_positions[i + 1][0] if i + 1 < len(split_positions) else len(text)
        segment_text = text[pos:end].strip()
        segment_words = len(segment_text.split())
        if segment_words >= TOC_THRESHOLD_WORDS:
            filtered_positions.append((pos, title))

    if not filtered_positions:
        word_count = len(text.split())
        logger.info("All detected segments were too short; treating entire text as Chapter 1")
        return [{"chapter_number": 1, "title": None, "text": text, "word_count": word_count}]

    logger.info(
        f"Chapter detection: {len(split_positions)} raw matches, "
        f"{len(filtered_positions)} after TOC filter"
    )
    split_positions = filtered_positions

    # --- PRE-HEADER TEXT ---
    # If there's substantial text before the first detected chapter header,
    # capture it as a prologue chapter.
    raw_chapters = []
    first_pos = split_positions[0][0]
    pre_header_text = text[:first_pos].strip()
    pre_header_words = len(pre_header_text.split())
    if pre_header_words >= MIN_CHAPTER_WORDS:
        logger.info(f"Captured {pre_header_words} words of pre-header text as prologue/Chapter 1")
        raw_chapters.append({
            "title": None,
            "text": pre_header_text,
            "word_count": pre_header_words,
        })

    # Extract chapter texts from split positions
    for i, (pos, title) in enumerate(split_positions):
        end = split_positions[i + 1][0] if i + 1 < len(split_positions) else len(text)
        chapter_text = text[pos:end].strip()

        # Build a clean title — extract subtitle from the text if available
        clean_title = _extract_title(text, pos, title)

        raw_chapters.append({
            "title": clean_title,
            "text": chapter_text,
            "word_count": len(chapter_text.split()),
        })

    # Merge short chapters (< 200 words) with the next chapter
    merged = []
    carry = None
    for ch in raw_chapters:
        if carry is not None:
            ch["text"] = carry["text"] + "\n\n" + ch["text"]
            ch["word_count"] = len(ch["text"].split())
            if carry["title"] is not None:
                ch["title"] = carry["title"]
            carry = None

        if ch["word_count"] < MIN_CHAPTER_WORDS and ch is not raw_chapters[-1]:
            carry = ch
        else:
            merged.append(ch)

    if carry is not None:
        if merged:
            merged[-1]["text"] += "\n\n" + carry["text"]
            merged[-1]["word_count"] = len(merged[-1]["text"].split())
        else:
            merged.append(carry)

    # Cap at MAX_CHAPTERS
    if len(merged) > MAX_CHAPTERS:
        logger.warning(
            f"Chapter detection yielded {len(merged)} chapters (max {MAX_CHAPTERS}). "
            "Falling back to single chapter."
        )
        full_text = "\n\n".join(ch["text"] for ch in merged)
        return [{"chapter_number": 1, "title": None, "text": full_text, "word_count": len(full_text.split())}]

    # Assign chapter numbers
    for i, ch in enumerate(merged):
        ch["chapter_number"] = i + 1

    logger.info(f"Detected {len(merged)} chapters")
    return merged


# ---------------------------------------------------------------------------
# Sync alias for tests that use the old regex-based detection directly
# ---------------------------------------------------------------------------

detect_chapters_sync = _detect_chapters_regex


# ---------------------------------------------------------------------------
# LLM-assisted structure detection (DECISION_007)
# ---------------------------------------------------------------------------

SPLITTING_PROMPT_PATH = Path(__file__).parent.parent / "analysis" / "prompts" / "splitting_v1.txt"
SAMPLE_START_WORDS = 3000
SAMPLE_END_WORDS = 1000
SAMPLE_FULL_THRESHOLD = 5000
AUTO_SPLIT_TARGET_WORDS = 4000
AUTO_SPLIT_WINDOW = 500  # words to search for a good break point
SPLITTING_MAX_TOKENS = 4096

# Visual separator patterns for auto-split
_VISUAL_SEPARATORS = re.compile(
    r"(?:^|\n)[ \t]*(?:\* \* \*|---+|___+|===+|###|~ ~ ~|• • •)[ \t]*(?:\n|$)",
    re.MULTILINE,
)


def _sample_manuscript(text: str) -> str:
    """Extract a representative sample from the manuscript for structure detection."""
    words = text.split()
    if len(words) <= SAMPLE_FULL_THRESHOLD:
        return text

    # First ~3000 words
    start_sample = " ".join(words[:SAMPLE_START_WORDS])
    # Find the actual character position for a clean break
    start_end = 0
    word_count = 0
    for i, ch in enumerate(text):
        if ch in (" ", "\n", "\r", "\t"):
            word_count += 1
            if word_count >= SAMPLE_START_WORDS:
                start_end = i
                break
    if start_end == 0:
        start_end = len(text)
    start_text = text[:start_end]

    # Last ~1000 words
    end_start = len(text)
    word_count = 0
    for i in range(len(text) - 1, -1, -1):
        if text[i] in (" ", "\n", "\r", "\t"):
            word_count += 1
            if word_count >= SAMPLE_END_WORDS:
                end_start = i
                break
    end_text = text[end_start:]

    return start_text + "\n\n[... middle of manuscript omitted ...]\n\n" + end_text


def _sanitize_sample(text: str) -> str:
    """Escape closing manuscript_sample tags to prevent prompt injection."""
    return text.replace("</manuscript_sample>", "&lt;/manuscript_sample&gt;")


def _normalize_whitespace(s: str) -> str:
    """Collapse all whitespace (including newlines) to single spaces and strip."""
    return re.sub(r"\s+", " ", s).strip()


def _find_marker_position(text: str, marker: str, search_start: int = 0) -> int:
    """Find a marker in text, skipping the front matter / ToC region.

    Per JUDGE: prefer occurrences NOT in the first 5% of text.
    Tries exact match first, then normalized whitespace match,
    then first-line-only match for multi-line markers.
    Returns character position or -1 if not found.
    """
    toc_boundary = int(len(text) * 0.05)

    # Strategy 1: exact match after ToC region
    pos = text.find(marker, max(search_start, toc_boundary))
    if pos != -1:
        return pos

    # Strategy 2: exact match anywhere after search_start
    pos = text.find(marker, search_start)
    if pos != -1:
        return pos

    # Strategy 3: try matching just the first line of the marker
    # (LLM sometimes returns multi-line markers)
    first_line = marker.split("\n")[0].strip()
    if first_line and first_line != marker.strip():
        pos = text.find(first_line, max(search_start, toc_boundary))
        if pos != -1:
            logger.info(f"Matched marker via first line: {first_line!r}")
            return pos
        pos = text.find(first_line, search_start)
        if pos != -1:
            logger.info(f"Matched marker via first line (pre-ToC): {first_line!r}")
            return pos

    # Strategy 4: case-insensitive exact match
    marker_lower = marker.lower()
    text_lower = text.lower()
    pos = text_lower.find(marker_lower, max(search_start, toc_boundary))
    if pos != -1:
        logger.info(f"Matched marker via case-insensitive match: {marker[:60]!r}")
        return pos
    pos = text_lower.find(marker_lower, search_start)
    if pos != -1:
        logger.info(f"Matched marker via case-insensitive match (pre-ToC): {marker[:60]!r}")
        return pos

    # Strategy 5: normalized whitespace + case-insensitive matching
    marker_normalized = _normalize_whitespace(marker)
    if marker_normalized:
        # Escape regex chars and replace spaces with \s+
        parts = [re.escape(word) for word in marker_normalized.split()]
        pattern = r"\s+".join(parts)
        try:
            for match in re.finditer(pattern, text[max(search_start, toc_boundary):], re.IGNORECASE):
                actual_pos = max(search_start, toc_boundary) + match.start()
                logger.info(f"Matched marker via whitespace+case normalization: {marker[:60]!r}")
                return actual_pos
            for match in re.finditer(pattern, text[search_start:], re.IGNORECASE):
                actual_pos = search_start + match.start()
                logger.info(f"Matched marker via whitespace+case normalization (pre-ToC): {marker[:60]!r}")
                return actual_pos
        except re.error:
            pass

    return -1


def _infer_missing_markers(
    text: str, positions: list[tuple[int, str]], search_start: int
) -> list[tuple[int, str]]:
    """Detect sequential patterns in markers and fill in gaps.

    If we found "ACT I", "ACT III", "ACT IV", "ACT V", infer "ACT II" is missing
    and search for it. Works with Roman numerals, Arabic numbers, and word numbers.
    """
    if len(positions) < 2:
        return positions

    titles = [title for _, title in positions]

    # Try to extract a common prefix + numbering pattern
    # Check for Roman numeral pattern: "ACT I", "ACT III", etc.
    roman_pattern = re.compile(r"^(.+?\s+)(I{1,3}|IV|V|VI{0,3}|IX|X|XI{0,3}|XIV|XV)$", re.IGNORECASE)
    arabic_pattern = re.compile(r"^(.+?\s+)(\d+)(.*)$")

    # Try Roman numerals — only require 2+ titles to match, not all
    roman_matched = [(i, m) for i, m in enumerate(roman_pattern.match(t) for t in titles) if m is not None]
    if len(roman_matched) >= 2:
        # Use the most common prefix among matched titles
        prefixes = [m.group(1) for _, m in roman_matched]
        prefix = max(set(prefixes), key=prefixes.count)

        found_numerals = set()
        max_val = 0
        for _, m in roman_matched:
            if m.group(1) == prefix:
                val = _roman_to_int(m.group(2).upper())
                if val > 0:
                    found_numerals.add(val)
                    max_val = max(max_val, val)

        # Find gaps
        all_positions = list(positions)
        for val in range(1, max_val + 1):
            if val not in found_numerals:
                numeral = _int_to_roman(val)
                candidate = prefix + numeral
                pos = _find_marker_position(text, candidate, search_start)
                if pos != -1:
                    logger.info(f"Inferred missing marker: {candidate!r} at position {pos}")
                    all_positions.append((pos, candidate))
                else:
                    logger.warning(f"Could not find inferred marker: {candidate!r}")

        if len(all_positions) > len(positions):
            all_positions.sort(key=lambda x: x[0])
            return all_positions

    # Try Arabic numerals — only require 2+ titles to match
    arabic_matched = [(i, m) for i, m in enumerate(arabic_pattern.match(t) for t in titles) if m is not None]
    if len(arabic_matched) >= 2:
        # Use most common prefix/suffix
        prefix = arabic_matched[0][1].group(1)
        suffix = arabic_matched[0][1].group(3)
        found_nums = set()
        max_num = 0
        for _, m in arabic_matched:
            num = int(m.group(2))
            found_nums.add(num)
            max_num = max(max_num, num)

        all_positions = list(positions)
        for num in range(1, max_num + 1):
            if num not in found_nums:
                candidate = f"{prefix}{num}{suffix}"
                pos = _find_marker_position(text, candidate, search_start)
                if pos != -1:
                    logger.info(f"Inferred missing marker: {candidate!r} at position {pos}")
                    all_positions.append((pos, candidate))

        if len(all_positions) > len(positions):
            all_positions.sort(key=lambda x: x[0])
            return all_positions

    return positions


def _roman_to_int(s: str) -> int:
    """Convert a Roman numeral string to integer."""
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    prev = 0
    for c in reversed(s.upper()):
        val = values.get(c, 0)
        if val < prev:
            total -= val
        else:
            total += val
        prev = val
    return total


def _int_to_roman(n: int) -> str:
    """Convert an integer to a Roman numeral string."""
    pairs = [
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    result = ""
    for value, numeral in pairs:
        while n >= value:
            result += numeral
            n -= value
    return result


def _split_by_markers(text: str, sections: list[dict], front_matter_end_marker: str | None) -> list[dict]:
    """Split text using LLM-provided section markers.

    Returns list of {chapter_number, title, text, word_count, split_method} or
    empty list if markers can't be matched.
    """
    if not sections:
        return []

    # Determine where to start searching (after front matter)
    search_start = 0
    if front_matter_end_marker:
        fm_pos = _find_marker_position(text, front_matter_end_marker, 0)
        if fm_pos != -1:
            search_start = fm_pos + len(front_matter_end_marker)
            logger.info(f"Front matter ends at position {search_start}")

    # Find positions for each marker
    positions = []
    for section in sections:
        marker = section.get("marker", "")
        title = section.get("title", marker)
        if not marker:
            continue

        pos = _find_marker_position(text, marker, search_start)
        if pos == -1:
            logger.warning(f"Could not find marker in text: {marker[:80]!r}")
            continue

        positions.append((pos, title))

    if not positions:
        logger.warning("No LLM markers could be matched in the text")
        return []

    # Sort by position and deduplicate
    positions.sort(key=lambda x: x[0])
    deduped = [positions[0]]
    for pos, title in positions[1:]:
        if pos - deduped[-1][0] > 20:
            deduped.append((pos, title))
    positions = deduped

    # Infer missing markers from patterns in found markers
    positions = _infer_missing_markers(text, positions, search_start)

    # Build chapters from positions
    chapters = []
    for i, (pos, title) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        chapter_text = text[pos:end].strip()
        word_count = len(chapter_text.split())

        chapters.append({
            "chapter_number": i + 1,
            "title": title,
            "text": chapter_text,
            "word_count": word_count,
            "split_method": "llm",
        })

    logger.info(f"LLM splitting produced {len(chapters)} sections")
    return chapters


def _auto_split(text: str) -> list[dict]:
    """Split text into ~4000-word chunks at natural break points.

    Prefers splitting at visual separators (* * *, ---, etc.) or paragraph
    boundaries. Used as fallback when LLM splitting fails.
    """
    words = text.split()
    total_words = len(words)

    if total_words <= AUTO_SPLIT_TARGET_WORDS:
        return [{
            "chapter_number": 1,
            "title": None,
            "text": text,
            "word_count": total_words,
            "split_method": "auto",
        }]

    # Find all visual separator positions
    separator_positions = set()
    for match in _VISUAL_SEPARATORS.finditer(text):
        separator_positions.add(match.start())

    # Find all paragraph break positions (double newline)
    para_breaks = set()
    for match in re.finditer(r"\n\s*\n", text):
        para_breaks.add(match.start())

    chapters = []
    current_start = 0
    chapter_num = 1

    while current_start < len(text):
        remaining_text = text[current_start:]
        remaining_words = len(remaining_text.split())

        # If remaining text fits in one chunk, take it all
        if remaining_words <= AUTO_SPLIT_TARGET_WORDS + AUTO_SPLIT_WINDOW:
            chapters.append({
                "chapter_number": chapter_num,
                "title": f"Section {chapter_num}",
                "text": remaining_text.strip(),
                "word_count": remaining_words,
                "split_method": "auto",
            })
            break

        # Find the target character position for ~4000 words
        word_count = 0
        target_pos = len(text)
        for i in range(current_start, len(text)):
            if text[i] in (" ", "\n"):
                word_count += 1
                if word_count >= AUTO_SPLIT_TARGET_WORDS:
                    target_pos = i
                    break

        # Search for a visual separator near the target
        best_split = None
        window_start = max(current_start, target_pos - AUTO_SPLIT_WINDOW * 6)  # ~6 chars per word
        window_end = min(len(text), target_pos + AUTO_SPLIT_WINDOW * 6)

        for sep_pos in separator_positions:
            if window_start <= sep_pos <= window_end:
                if best_split is None or abs(sep_pos - target_pos) < abs(best_split - target_pos):
                    best_split = sep_pos

        # If no separator, find nearest paragraph break
        if best_split is None:
            for pb_pos in para_breaks:
                if window_start <= pb_pos <= window_end:
                    if best_split is None or abs(pb_pos - target_pos) < abs(best_split - target_pos):
                        best_split = pb_pos

        # If still nothing, just split at target
        if best_split is None:
            best_split = target_pos

        chunk_text = text[current_start:best_split].strip()
        if chunk_text:
            chapters.append({
                "chapter_number": chapter_num,
                "title": f"Section {chapter_num}",
                "text": chunk_text,
                "word_count": len(chunk_text.split()),
                "split_method": "auto",
            })
            chapter_num += 1

        current_start = best_split

    logger.info(f"Auto-split produced {len(chapters)} sections at ~{AUTO_SPLIT_TARGET_WORDS} words each")
    return chapters


def _merge_short_sections(chapters: list[dict]) -> list[dict]:
    """Merge sections shorter than MIN_CHAPTER_WORDS with the next section."""
    if not chapters:
        return chapters

    merged = []
    carry = None
    for ch in chapters:
        if carry is not None:
            ch["text"] = carry["text"] + "\n\n" + ch["text"]
            ch["word_count"] = len(ch["text"].split())
            if carry["title"] is not None:
                ch["title"] = carry["title"]
            carry = None

        if ch["word_count"] < MIN_CHAPTER_WORDS and ch is not chapters[-1]:
            carry = ch
        else:
            merged.append(ch)

    if carry is not None:
        if merged:
            merged[-1]["text"] += "\n\n" + carry["text"]
            merged[-1]["word_count"] = len(merged[-1]["text"].split())
        else:
            merged.append(carry)

    # Renumber
    for i, ch in enumerate(merged):
        ch["chapter_number"] = i + 1

    return merged


async def detect_chapters(text: str) -> tuple[list[dict], list[str]]:
    """Detect chapter/section boundaries using LLM-assisted structure detection.

    Returns (chapters_list, warnings_list).
    Each chapter dict has: chapter_number, title, text, word_count, split_method.

    Fallback chain per DECISION_007:
    1. LLM-assisted splitting (sends sample to LLM for structure detection)
    2. Auto-split at ~4K words at natural break points
    3. Regex-based chapter detection (legacy)
    """
    from app.analysis.llm_client import LLMError, call_llm
    from app.analysis.json_repair import parse_json_response
    from app.config import settings

    warnings = []
    chapters = []

    # --- Tier 1: LLM-assisted splitting ---
    try:
        model = settings.llm_model_splitting or settings.llm_model_analysis
        sample = _sample_manuscript(text)
        sanitized = _sanitize_sample(sample)

        prompt_template = SPLITTING_PROMPT_PATH.read_text()
        prompt = prompt_template.format(manuscript_sample=sanitized)

        logger.info(f"Calling LLM for structure detection (model={model}, sample_len={len(sample)})")
        raw_response = await call_llm(prompt, model, SPLITTING_MAX_TOKENS)

        parsed = parse_json_response(raw_response)
        if parsed is None:
            logger.warning(f"LLM splitting response was not valid JSON: {raw_response[:200]!r}")
        else:
            manuscript_type = parsed.get("manuscript_type", "unknown")
            structure_desc = parsed.get("structure_description", "")
            sections = parsed.get("sections", [])
            front_matter_end = parsed.get("front_matter_end_marker")

            logger.info(
                f"LLM detected manuscript_type={manuscript_type}, "
                f"structure={structure_desc!r}, sections={len(sections)}"
            )

            if sections:
                chapters = _split_by_markers(text, sections, front_matter_end)

    except LLMError as e:
        logger.warning(f"LLM splitting failed: {e}")
        warnings.append(
            "Structure detection encountered an error. Your manuscript "
            "has been split using basic pattern matching. You may want to retry."
        )
    except Exception as e:
        logger.error(f"Unexpected error in LLM splitting: {e}", exc_info=True)
        warnings.append(
            "Structure detection encountered an error. Your manuscript "
            "has been split using basic pattern matching. You may want to retry."
        )

    # --- Tier 2: Auto-split fallback ---
    if not chapters:
        if not warnings:
            # LLM returned no sections (not an error, just no structure found)
            warnings.append(
                "No clear section structure was detected in your manuscript. "
                "It has been automatically divided into sections for analysis. "
                "Results may be less accurate."
            )
        chapters = _auto_split(text)

    # --- Tier 3: If auto-split produces only 1 section, try regex ---
    if len(chapters) == 1:
        regex_chapters = _detect_chapters_regex(text)
        if len(regex_chapters) > 1:
            # Regex found structure that auto-split missed
            logger.info(f"Regex fallback found {len(regex_chapters)} chapters")
            for ch in regex_chapters:
                ch["split_method"] = "regex"
            chapters = regex_chapters
            # Clear the "no structure" warning since regex found some
            warnings = []

    # --- Post-processing ---
    chapters = _merge_short_sections(chapters)

    # Cap at MAX_CHAPTERS
    if len(chapters) > MAX_CHAPTERS:
        logger.warning(
            f"Splitting yielded {len(chapters)} sections (max {MAX_CHAPTERS}). "
            "Falling back to single section."
        )
        full_text = "\n\n".join(ch["text"] for ch in chapters)
        chapters = [{
            "chapter_number": 1,
            "title": None,
            "text": full_text,
            "word_count": len(full_text.split()),
            "split_method": "auto",
        }]

    # Ensure split_method is set on all chapters
    for ch in chapters:
        ch.setdefault("split_method", "unknown")

    logger.info(
        f"Final split: {len(chapters)} sections "
        f"(method={chapters[0].get('split_method', 'unknown') if chapters else 'none'})"
    )
    return chapters, warnings


def check_word_count(chapters: list[dict]) -> int:
    """Sum word counts and check against limit. Returns total word count or raises."""
    total = sum(ch["word_count"] for ch in chapters)
    if total > MAX_WORD_COUNT:
        raise ExtractionError(
            f"Manuscript exceeds {MAX_WORD_COUNT:,} word limit ({total:,} words detected). "
            "Consider splitting into separate uploads."
        )
    return total


class ExtractionError(Exception):
    """Raised when text extraction fails in a user-facing way."""
    pass
