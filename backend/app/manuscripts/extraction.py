"""Text extraction and chapter detection for manuscripts.

Per DECISION_003 JUDGE amendments:
- No bare-number regex for chapter detection.
- Minimum 200 words per chapter (merge short sections with next).
- Cap at 100 chapters; fall back to single chapter if exceeded.

Post-eval fixes (Gutenberg testing 2026-03-11):
- Filter out TOC entries: chapter headers with < 50 words before the next header
  are likely table-of-contents lines, not real chapter boundaries.
- Capture pre-first-header text as Chapter 1 if it has >= 200 words.
"""

import io
import logging
import re

from docx import Document as DocxDocument
from PyPDF2 import PdfReader

logger = logging.getLogger(__name__)

CHAPTER_PATTERNS = [
    re.compile(r"^Chapter\s+\d+", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Chapter\s+[A-Z][a-z]+", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^CHAPTER\s+", re.MULTILINE),
]

MIN_CHAPTER_WORDS = 200
TOC_THRESHOLD_WORDS = 50  # Chapters with fewer words than this are likely TOC entries
MAX_CHAPTERS = 150
MAX_WORD_COUNT = 120_000


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


MIN_EXTRACTED_WORDS = 50  # Minimum words after extraction to be considered valid
LANGUAGE_SAMPLE_SIZE = 5000  # Characters to sample for language detection


def detect_language(text: str) -> str | None:
    """Detect the language of text. Returns ISO 639-1 code or None on failure.

    Per blueprint: 'Non-English manuscript detection before Claude analysis —
    return error, don't analyze.'
    """
    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0  # Deterministic results
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

    # Validate extracted text has meaningful content
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

    # Language detection — reject non-English manuscripts
    lang = detect_language(text)
    if lang is not None and lang != "en":
        logger.info(f"Non-English manuscript detected: language={lang}")
        raise ExtractionError(
            "GhostEditor currently supports English-language manuscripts only. "
            f"This text was detected as '{lang}'."
        )

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
        raise  # Re-raise our own errors (e.g., scanned PDF detection)
    except Exception as e:
        raise ExtractionError(
            "Could not read this PDF file. It may be password-protected or damaged. "
            "Try exporting as a new PDF from your word processor."
        )


def detect_chapters(text: str) -> list[dict]:
    """Detect chapter boundaries in text. Returns list of {chapter_number, title, text, word_count}.

    Per JUDGE: merge chapters < 200 words with next; cap at 100; no bare-number regex.
    """
    split_positions = []

    for pattern in CHAPTER_PATTERNS:
        for match in pattern.finditer(text):
            split_positions.append((match.start(), match.group().strip()))

    if not split_positions:
        word_count = len(text.split())
        logger.info("No chapter headers detected; treating entire text as Chapter 1")
        return [{"chapter_number": 1, "title": None, "text": text, "word_count": word_count}]

    # Sort by position, deduplicate overlapping matches
    split_positions.sort(key=lambda x: x[0])
    # Remove duplicates that are within 50 chars of each other
    deduped = [split_positions[0]]
    for pos, title in split_positions[1:]:
        if pos - deduped[-1][0] > 50:
            deduped.append((pos, title))
    split_positions = deduped

    # --- TOC FILTER ---
    # If many consecutive "chapters" have very little text between headers,
    # they're likely table-of-contents entries. Filter them out.
    # A real chapter has substantial text; a TOC line has ~1-10 words.
    filtered_positions = []
    for i, (pos, title) in enumerate(split_positions):
        end = split_positions[i + 1][0] if i + 1 < len(split_positions) else len(text)
        segment_text = text[pos:end].strip()
        segment_words = len(segment_text.split())
        if segment_words >= TOC_THRESHOLD_WORDS:
            filtered_positions.append((pos, title))

    if not filtered_positions:
        # All segments were tiny — possibly a very fragmented text
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
    # capture it as Chapter 1 (handles cases like P&P where Chapter I header is missing).
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
        raw_chapters.append({"title": title, "text": chapter_text, "word_count": len(chapter_text.split())})

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
