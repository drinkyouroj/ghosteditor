import pytest
from pydantic import ValidationError

from app.analysis.bible_schema import StoryBibleSchema


class TestStoryBibleSchema:
    def test_valid_minimal_bible(self):
        data = {
            "characters": [],
            "timeline": [],
            "settings": [],
            "world_rules": [],
            "voice_profile": {"pov": "third person", "tense": "past", "tone": "dark", "style_notes": ""},
            "plot_threads": [],
        }
        bible = StoryBibleSchema.model_validate(data)
        assert bible.voice_profile.pov == "third person"

    def test_valid_full_bible(self):
        data = {
            "characters": [
                {
                    "name": "Alice",
                    "aliases": ["Al"],
                    "description": "The protagonist",
                    "first_appearance": "Chapter 1",
                    "role": "protagonist",
                    "traits": ["brave", "curious"],
                    "physical": {"age": "30", "gender": "female", "appearance": "tall with dark hair"},
                    "relationships": [{"to": "Bob", "type": "friend"}],
                }
            ],
            "timeline": [
                {
                    "event": "Alice arrives in town",
                    "chapter": 1,
                    "date_in_story": "March 2024",
                    "characters_involved": ["Alice"],
                }
            ],
            "settings": [{"name": "The Town", "description": "A small coastal town", "chapter_introduced": 1}],
            "world_rules": ["Magic requires spoken words"],
            "voice_profile": {"pov": "first person", "tense": "present", "tone": "suspenseful", "style_notes": "short sentences"},
            "plot_threads": [
                {
                    "thread": "Why did Alice come to town?",
                    "status": "open",
                    "introduced_chapter": 1,
                    "last_updated_chapter": 1,
                }
            ],
        }
        bible = StoryBibleSchema.model_validate(data)
        assert len(bible.characters) == 1
        assert bible.characters[0].name == "Alice"
        assert bible.characters[0].physical.age == "30"

    def test_missing_optional_fields_get_defaults(self):
        """Schema should provide defaults for missing optional fields."""
        data = {
            "characters": [{"name": "Bob"}],
            "timeline": [],
            "settings": [],
            "world_rules": [],
            "voice_profile": {},
            "plot_threads": [],
        }
        bible = StoryBibleSchema.model_validate(data)
        assert bible.characters[0].aliases == []
        assert bible.characters[0].role == "minor"
        assert bible.characters[0].physical.age is None

    def test_empty_dict_produces_valid_bible(self):
        """An empty dict should produce a bible with all default values."""
        bible = StoryBibleSchema.model_validate({})
        assert bible.characters == []
        assert bible.world_rules == []

    def test_integer_coercion_for_chapter(self):
        """String chapter numbers should be coerced to int."""
        data = {
            "timeline": [{"event": "test", "chapter": "1"}],
        }
        bible = StoryBibleSchema.model_validate(data)
        assert bible.timeline[0].chapter == 1
