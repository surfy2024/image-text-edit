from edit_chart_text import __version__


def test_package_version() -> None:
    assert __version__ == "0.1.0"




def test_skill_documents_safe_substring_confirmation_fields() -> None:
    from pathlib import Path

    skill_text = (Path(__file__).parents[1] / "SKILL.md").read_text(encoding="utf-8")

    assert "match_mode" in skill_text
    assert "substring" in skill_text
    assert "substring_occurrence" in skill_text

def test_default_runtime_dependencies_include_ocr_engine_and_lock():
    import tomllib
    from pathlib import Path
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [item.lower() for item in metadata["project"]["dependencies"]]
    assert any(item.startswith("paddleocr") for item in dependencies)
    assert any(item.startswith("paddlepaddle>=3.0,<4") for item in dependencies)
    assert any(item.startswith("filelock") for item in dependencies)
