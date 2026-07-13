from edit_chart_text import __version__


def test_package_version() -> None:
    assert __version__ == "0.1.0"




def test_default_runtime_dependencies_include_ocr_engine_and_lock():
    import tomllib
    from pathlib import Path
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [item.lower() for item in metadata["project"]["dependencies"]]
    assert any(item.startswith("paddleocr") for item in dependencies)
    assert any(item.startswith("paddlepaddle>=3.0,<4") for item in dependencies)
    assert any(item.startswith("filelock") for item in dependencies)
