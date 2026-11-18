from pathlib import Path


def test_version_two_does_not_collect_clinical_flags():
    root = Path(__file__).resolve().parents[1] / "bot"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    for token in (
        "erp_required",
        "md_required",
        "psychiatrist_first",
        "coordinate_with_doctor",
        "EMDR",
        "схема-терапия",
    ):
        assert token not in source
