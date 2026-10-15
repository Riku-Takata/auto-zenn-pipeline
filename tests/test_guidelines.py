import re
from pathlib import Path

GUIDELINES_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "writing_guidelines.md"
)
RULE_ID_PATTERN = re.compile(r"^### (Rule-[A-Z]\d{2}):", re.MULTILINE)
EXPECTED_IDS = [
    "Rule-S01",
    "Rule-S02",
    "Rule-T01",
    "Rule-T02",
    "Rule-M01",
    "Rule-M02",
]


def _rule_ids() -> list[str]:
    content = GUIDELINES_PATH.read_text(encoding="utf-8")
    return RULE_ID_PATTERN.findall(content)


def test_guidelines_file_exists() -> None:
    assert GUIDELINES_PATH.is_file(), f"Guidelines file not found at {GUIDELINES_PATH}"


def test_rule_ids_are_unique() -> None:
    ids = _rule_ids()
    assert len(ids) == 6, f"Expected 6 rule IDs, got {len(ids)}: {ids}"
    assert len(set(ids)) == len(ids), f"Duplicate rule IDs found in {ids}"


def test_rule_ids_match_expected_set() -> None:
    ids = _rule_ids()
    assert set(ids) == set(EXPECTED_IDS), (
        f"Rule IDs set mismatch: got {set(ids)}, expected {set(EXPECTED_IDS)}"
    )


def test_rule_ids_in_expected_order() -> None:
    ids = _rule_ids()
    assert ids == EXPECTED_IDS, (
        f"Rule IDs order mismatch: got {ids}, expected {EXPECTED_IDS}"
    )
