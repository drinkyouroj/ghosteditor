"""Genre convention templates for chapter analysis.

Maps genre names to lists of key developmental editing conventions
that the chapter analysis prompt uses to evaluate genre fit.
"""

GENRE_CONVENTIONS: dict[str, list[str]] = {
    "Romance": [
        "Central romantic relationship introduced early",
        "Emotional stakes established alongside or above external stakes",
        "Meet-cute or inciting romantic incident present",
        "Internal conflict or emotional vulnerability shown in point-of-view character",
        "Chemistry or tension between romantic leads demonstrated through interaction",
        "Emotionally satisfying ending or clear trajectory toward one",
        "Dual POV or strong insight into both leads' motivations",
    ],
    "Fantasy": [
        "World-building details woven into action rather than info-dumped",
        "Magic system or supernatural elements have clear constraints or costs",
        "Protagonist's goal or quest established early",
        "Sense of wonder or discovery present in setting descriptions",
        "Power structures or political dynamics hinted at or established",
        "Distinct cultures or peoples differentiated through behavior and language",
        "Foreshadowing of larger conflict or threat",
    ],
    "Thriller": [
        "Hook within the first chapter — immediate stakes or danger",
        "Clear stakes and consequences of failure established",
        "Protagonist goal established early with urgency",
        "Ticking clock or escalating pressure present",
        "Short chapters or scenes that maintain momentum",
        "Antagonist force introduced or foreshadowed",
        "Cliffhanger or unresolved tension at chapter end",
        "Information revealed in controlled doses to build suspense",
    ],
    "Mystery": [
        "Central crime, puzzle, or question established early",
        "Clues planted — at least one fair clue per chapter",
        "Red herrings or misdirection present without being unfair",
        "Investigator or protagonist has a clear method of inquiry",
        "Suspect pool introduced or expanded",
        "Each chapter raises new questions while partially answering old ones",
        "Setting details that could serve as clues or atmosphere",
    ],
    "Literary Fiction": [
        "Prose style is distinctive and intentional",
        "Character interiority is deep — thoughts, emotions, sensory experience",
        "Themes or motifs introduced through imagery rather than stated",
        "Conflict is primarily internal or interpersonal rather than external",
        "Subtext present in dialogue — characters don't say exactly what they mean",
        "Pacing serves emotional resonance over plot momentum",
    ],
    "Science Fiction": [
        "Speculative element introduced with internal consistency",
        "Technology or science grounded in plausible extrapolation or clear rules",
        "World-building integrated into character experience, not lectured",
        "Social or philosophical implications of speculative element explored",
        "Protagonist's relationship to the speculative element is personal",
        "Sense of scale — the speculative world feels larger than the scene",
        "Foreshadowing of systemic or technological consequences",
    ],
    "Horror": [
        "Atmosphere of dread or unease established through sensory detail",
        "Source of horror introduced or foreshadowed early",
        "Vulnerability of characters is clear — they have something to lose",
        "Pacing alternates between tension and brief release",
        "Horror operates on multiple levels — physical, psychological, or existential",
        "Setting contributes to isolation or claustrophobia",
        "Normal world established before disruption, so stakes feel real",
    ],
    "Historical Fiction": [
        "Period details are specific and grounded, not generic",
        "Historical setting shapes character behavior and choices",
        "Language and dialogue feel era-appropriate without being archaic",
        "Social norms and constraints of the period create conflict",
        "Sensory details ground the reader in the historical world",
        "Historical events or context woven into personal narrative",
        "Characters feel of their time, not modern people in costume",
    ],
}

# Normalized lookup: lowercase, stripped keys for fuzzy matching
_NORMALIZED: dict[str, str] = {k.lower().strip(): k for k in GENRE_CONVENTIONS}


def get_genre_conventions(genre: str) -> list[str]:
    """Return conventions for the closest matching genre, or empty list for unknown.

    Matches case-insensitively and handles common variations
    (e.g., "romance", "ROMANCE", "sci-fi", "science-fiction").
    """
    if not genre:
        return []

    normalized = genre.lower().strip()

    # Direct match
    if normalized in _NORMALIZED:
        return GENRE_CONVENTIONS[_NORMALIZED[normalized]]

    # Common aliases
    aliases: dict[str, str] = {
        "sci-fi": "Science Fiction",
        "scifi": "Science Fiction",
        "science-fiction": "Science Fiction",
        "sf": "Science Fiction",
        "lit fic": "Literary Fiction",
        "litfic": "Literary Fiction",
        "literary": "Literary Fiction",
        "historical": "Historical Fiction",
        "hist fic": "Historical Fiction",
        "suspense": "Thriller",
        "crime": "Mystery",
        "detective": "Mystery",
        "dark fantasy": "Fantasy",
        "urban fantasy": "Fantasy",
        "epic fantasy": "Fantasy",
        "paranormal romance": "Romance",
        "romantic suspense": "Romance",
        "gothic": "Horror",
        "gothic horror": "Horror",
    }

    if normalized in aliases:
        return GENRE_CONVENTIONS[aliases[normalized]]

    # Substring match — check if any canonical genre name appears in the input
    for canonical_lower, canonical in _NORMALIZED.items():
        if canonical_lower in normalized or normalized in canonical_lower:
            return GENRE_CONVENTIONS[canonical]

    return []
