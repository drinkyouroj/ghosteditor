"""Extract first 3 chapters from each Gutenberg sample for eval testing.

Strips Gutenberg headers/footers and saves clean chapter text.
Also runs the chapter detection and extraction pipeline as a smoke test.
"""

import json
import re
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.manuscripts.extraction import detect_chapters, extract_text_from_txt, check_word_count

SAMPLES_DIR = Path(__file__).parent / "samples"

# Gutenberg header/footer markers
START_MARKER = "*** START OF THE PROJECT GUTENBERG EBOOK"
END_MARKER = "*** END OF THE PROJECT GUTENBERG EBOOK"

BOOKS = {
    "romance": {
        "file": "pride_and_prejudice_full.txt",
        "title": "Pride and Prejudice",
        "author": "Jane Austen",
        "genre": "romance",
    },
    "fantasy": {
        "file": "princess_of_mars_full.txt",
        "title": "A Princess of Mars",
        "author": "Edgar Rice Burroughs",
        "genre": "fantasy",
    },
    "literary": {
        "file": "moby_dick_full.txt",
        "title": "Moby Dick",
        "author": "Herman Melville",
        "genre": "literary fiction",
    },
    "thriller": {
        "file": "thirty_nine_steps_full.txt",
        "title": "The Thirty-Nine Steps",
        "author": "John Buchan",
        "genre": "thriller",
    },
    "mystery": {
        "file": "hound_of_baskervilles_full.txt",
        "title": "The Hound of the Baskervilles",
        "author": "Arthur Conan Doyle",
        "genre": "mystery",
    },
}


def strip_gutenberg(text: str) -> str:
    """Remove Project Gutenberg header and footer."""
    # Find start
    start_idx = text.find(START_MARKER)
    if start_idx != -1:
        # Skip past the marker line
        newline_after = text.find("\n", start_idx)
        if newline_after != -1:
            text = text[newline_after + 1:]

    # Find end
    end_idx = text.find(END_MARKER)
    if end_idx != -1:
        text = text[:end_idx]

    return text.strip()


def extract_first_n_chapters(text: str, n: int = 3) -> str:
    """Extract first N chapters from text using chapter detection."""
    chapters = detect_chapters(text)

    if len(chapters) <= n:
        return text

    # Get text up to the end of chapter N
    combined = "\n\n".join(ch["text"] for ch in chapters[:n])
    return combined


def main():
    results = {}

    for genre_key, book_info in BOOKS.items():
        filepath = SAMPLES_DIR / book_info["file"]
        if not filepath.exists():
            print(f"SKIP: {book_info['title']} — file not found at {filepath}")
            continue

        print(f"\n{'='*60}")
        print(f"Processing: {book_info['title']} ({genre_key})")
        print(f"{'='*60}")

        raw = filepath.read_text(encoding="utf-8-sig")
        clean = strip_gutenberg(raw)

        print(f"  Full text length: {len(clean):,} chars")
        print(f"  Full word count: {len(clean.split()):,} words")

        # Run chapter detection on full text
        all_chapters = detect_chapters(clean)
        print(f"  Chapters detected: {len(all_chapters)}")

        for ch in all_chapters[:5]:
            title_str = ch.get('title') or 'untitled'
            print(f"    Ch {ch['chapter_number']}: {title_str[:50]} ({ch['word_count']} words)")

        # Extract first 3 chapters
        first_chapters = all_chapters[:3]
        sample_text = "\n\n".join(ch["text"] for ch in first_chapters)

        # Save the extracted sample
        sample_path = SAMPLES_DIR / f"{genre_key}_sample.txt"
        sample_path.write_text(sample_text, encoding="utf-8")
        print(f"  Saved sample: {sample_path.name} ({len(sample_text.split()):,} words)")

        # Re-run chapter detection on the extracted sample to verify
        re_detected = detect_chapters(sample_text)
        print(f"  Re-detected chapters in sample: {len(re_detected)}")

        # Word count check
        total_words = sum(ch["word_count"] for ch in first_chapters)
        print(f"  Total words in sample: {total_words:,}")

        results[genre_key] = {
            "title": book_info["title"],
            "genre": book_info["genre"],
            "full_chapters_detected": len(all_chapters),
            "sample_chapters": len(first_chapters),
            "sample_word_count": total_words,
            "chapter_details": [
                {
                    "number": ch["chapter_number"],
                    "title": ch.get("title", ""),
                    "word_count": ch["word_count"],
                }
                for ch in first_chapters
            ],
        }

    # Save results summary
    results_path = SAMPLES_DIR.parent / "extraction_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n\nResults saved to {results_path}")
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for genre_key, data in results.items():
        print(f"  {data['title']:<35} {data['full_chapters_detected']:>3} chapters detected, "
              f"sample: {data['sample_word_count']:>6,} words in {data['sample_chapters']} chapters")


if __name__ == "__main__":
    main()
