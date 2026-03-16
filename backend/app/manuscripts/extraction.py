"""Text extraction and chapter detection for manuscripts.

Per DECISION_003 JUDGE amendments:
- No bare-number regex for chapter detection.
- Minimum 200 words per chapter (merge short sections with next).
- Cap at 150 chapters; fall back to single chapter if exceeded.

Chapter detection supports:
- "Chapter 1", "Chapter One", "CHAPTER I.", "CHAPTER XIV" formats
- Standalone Roman numerals on their own line (Gutenberg style)
- Gutenberg preamble/license stripping
- TOC filtering (short segments between headers)
- Full title capture including subtitles on the next line
"""

from __future__ import annotations

import io
import logging
import re

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


def detect_chapters(text: str) -> list[dict]:
    """Detect chapter boundaries in text. Returns list of {chapter_number, title, text, word_count}.

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
