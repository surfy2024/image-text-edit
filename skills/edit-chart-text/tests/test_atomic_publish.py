import errno
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import edit_chart_text.pipeline as pipeline
from edit_chart_text.models import EditRequest, Replacement, TextCandidate
from edit_chart_text.pipeline import run_pipeline


def candidate(text="HZ", x=10):
    return TextCandidate(text, ((x, 10), (x + 20, 10), (x + 20, 24), (x, 24)), .99)


def chart(tmp_path, second=False):
    path = tmp_path / "chart.png"
    image = Image.new("RGB", (80, 40), "white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), "HZ", fill="black")
    if second:
        draw.text((40, 10), "HZ", fill="black")
    image.save(path)
    return path


class SequenceOCR:
    def __init__(self, *results):
        self.results = list(results)

    def detect(self, _path):
        return self.results.pop(0)


def fail_first_matching_unlink(monkeypatch, fragment):
    real_unlink = Path.unlink
    failed = []

    def flaky_unlink(path, *args, **kwargs):
        if fragment in path.name and path.suffix == ".tmp" and not failed:
            failed.append(path)
            raise OSError("injected temporary unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    return failed


def test_edited_publish_survives_temporary_unlink_failure(tmp_path, monkeypatch):
    source = chart(tmp_path)
    failed = fail_first_matching_unlink(monkeypatch, "_edited-")

    report = run_pipeline(
        EditRequest(source, (Replacement("HZ", "CS", "one"),)),
        SequenceOCR((candidate(),), (candidate("CS"),)),
    )

    assert failed
    assert report.status == "success"
    assert Path(report.output_path).exists()
    assert json.loads(Path(report.report_path).read_text(encoding="utf-8"))["status"] == "success"


def test_preview_publish_survives_temporary_unlink_failure(tmp_path, monkeypatch):
    source = chart(tmp_path, second=True)
    failed = fail_first_matching_unlink(monkeypatch, "_candidates-")

    report = run_pipeline(
        EditRequest(source, (Replacement("HZ", "CS", "ask"),)),
        SequenceOCR((candidate(x=10), candidate(x=40))),
    )

    assert failed
    assert report.status == "needs_confirmation"
    assert Path(report.preview_path).exists()
    assert json.loads(Path(report.report_path).read_text(encoding="utf-8"))["status"] == "needs_confirmation"


def test_report_publish_survives_temporary_unlink_failure(tmp_path, monkeypatch):
    source = chart(tmp_path)
    failed = fail_first_matching_unlink(monkeypatch, "_edit-report-")

    report = run_pipeline(
        EditRequest(source, (Replacement("HZ", "CS", "one"),)),
        SequenceOCR((candidate(),), (candidate("CS"),)),
    )

    assert failed
    assert report.status == "success"
    assert Path(report.output_path).exists()
    assert json.loads(Path(report.report_path).read_text(encoding="utf-8"))["status"] == "success"


def unsupported_link(_source, _destination):
    raise OSError(errno.ENOTSUP, "hard links unsupported")


def test_windows_unsupported_hardlink_falls_back_without_replace(tmp_path, monkeypatch):
    temporary = tmp_path / ".artifact.tmp"
    destination = tmp_path / "artifact.json"
    temporary.write_bytes(b"new")
    monkeypatch.setattr(pipeline.os, "link", unsupported_link)
    monkeypatch.setattr(pipeline, "_platform_name", lambda: "nt")

    pipeline._publish_no_replace(temporary, destination)

    assert destination.read_bytes() == b"new"
    assert not temporary.exists()


def test_windows_unsupported_hardlink_collision_preserves_destination(tmp_path, monkeypatch):
    temporary = tmp_path / ".artifact.tmp"
    destination = tmp_path / "artifact.json"
    temporary.write_bytes(b"new")
    destination.write_bytes(b"user-owned")
    monkeypatch.setattr(pipeline.os, "link", unsupported_link)
    monkeypatch.setattr(pipeline, "_platform_name", lambda: "nt")

    with pytest.raises(FileExistsError, match="formal artifact already exists"):
        pipeline._publish_no_replace(temporary, destination)

    assert destination.read_bytes() == b"user-owned"
    assert temporary.read_bytes() == b"new"


def test_non_windows_unsupported_hardlink_has_actionable_error(tmp_path, monkeypatch):
    temporary = tmp_path / ".artifact.tmp"
    destination = tmp_path / "artifact.json"
    temporary.write_bytes(b"new")
    monkeypatch.setattr(pipeline.os, "link", unsupported_link)
    monkeypatch.setattr(pipeline, "_platform_name", lambda: "posix")

    with pytest.raises(pipeline.ArtifactPublishError) as raised:
        pipeline._publish_no_replace(temporary, destination)

    message = str(raised.value).lower()
    assert "hardlink" in message
    assert "local volume" in message
    assert "windows" in message
    assert not destination.exists()


def test_secret_partial_write_failure_never_creates_short_final(tmp_path, monkeypatch):
    state = tmp_path / "state"
    real_write = pipeline.os.write
    calls = 0

    def partial_then_fail(descriptor, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, data[:7])
        raise OSError("injected write failure")

    monkeypatch.setattr(pipeline.os, "write", partial_then_fail)

    with pytest.raises(OSError, match="write failure"):
        pipeline._load_or_create_secret(state)

    assert not (state / "install-secret.bin").exists()
    assert not tuple(state.glob(".install-secret-*.tmp"))


def test_secret_fsync_failure_never_creates_final(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(pipeline.os, "fsync", lambda _descriptor: (_ for _ in ()).throw(OSError("injected fsync failure")))

    with pytest.raises(OSError, match="fsync failure"):
        pipeline._load_or_create_secret(state)

    assert not (state / "install-secret.bin").exists()
    assert not tuple(state.glob(".install-secret-*.tmp"))


def test_secret_publish_failure_never_creates_final(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(
        pipeline,
        "_publish_no_replace",
        lambda _temporary, _destination: (_ for _ in ()).throw(OSError("injected publish failure")),
    )

    with pytest.raises(OSError, match="publish failure"):
        pipeline._load_or_create_secret(state)

    assert not (state / "install-secret.bin").exists()
    assert not tuple(state.glob(".install-secret-*.tmp"))


def test_secret_publish_collision_reads_complete_concurrent_winner(tmp_path, monkeypatch):
    state = tmp_path / "state"
    winner = b"w" * 32

    def publish_winner(_temporary, destination):
        destination.write_bytes(winner)
        raise FileExistsError("concurrent winner")

    monkeypatch.setattr(pipeline, "_publish_no_replace", publish_winner)

    assert pipeline._load_or_create_secret(state) == winner
    assert (state / "install-secret.bin").read_bytes() == winner
    assert not tuple(state.glob(".install-secret-*.tmp"))
