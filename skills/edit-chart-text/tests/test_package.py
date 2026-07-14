from edit_chart_text import __version__


def test_package_version() -> None:
    assert __version__ == "0.1.0"




def test_skill_documents_safe_substring_confirmation_fields() -> None:
    from pathlib import Path

    skill_text = (Path(__file__).parents[1] / "SKILL.md").read_text(encoding="utf-8")
    skill_lines = skill_text.splitlines()

    mode_rule = next(line for line in skill_lines if "match_mode=substring" in line)
    assert "match_mode=exact" in mode_rule

    matching_rule = next(line for line in skill_lines if "区分大小写" in line)
    assert "字面匹配" in matching_rule
    assert "不要使用正则表达式或模糊匹配" in matching_rule

    occurrence_rule = next(line for line in skill_lines if "多次出现 `old_text`" in line)
    assert "首次请求必须先返回候选报告" in occurrence_rule
    assert "用户自然语言中的序号或位置" in occurrence_rule
    assert "直接写入 `substring_occurrence`" in occurrence_rule
    assert "不要自行推断" in occurrence_rule

    confirmation_rule = next(line for line in skill_lines if line.startswith("8. "))
    assert "从同一候选记录原样复制确认字段" in confirmation_rule
    for field in (
        "candidate_token",
        "candidate_number",
        "polygon",
        "candidate_polygon",
        "substring_occurrence",
    ):
        assert f"`{field}`" in confirmation_rule


def test_default_runtime_dependencies_include_ocr_engine_and_lock():
    import tomllib
    from pathlib import Path
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [item.lower() for item in metadata["project"]["dependencies"]]
    assert any(item.startswith("paddleocr") for item in dependencies)
    assert any(item.startswith("paddlepaddle>=3.0,<4") for item in dependencies)
    assert any(item.startswith("filelock") for item in dependencies)
