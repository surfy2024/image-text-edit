import edit_chart_text.cli as cli
import edit_chart_text.pipeline as pipeline


def test_cli_returns_exit_four_with_actionable_unsupported_hardlink_error(monkeypatch, capsys):
    request = object()
    backend = object()
    message = (
        "hardlink publish is unsupported; copy the source to a hardlink-capable "
        "local volume. The no-replace rename fallback is Windows-only."
    )
    monkeypatch.setattr(cli, "load_request", lambda _path: request)
    monkeypatch.setattr(cli, "PaddleOCRBackend", lambda: backend)
    monkeypatch.setattr(
        cli,
        "run_pipeline",
        lambda _request, _backend: (_ for _ in ()).throw(
            pipeline.ArtifactPublishError(message)
        ),
    )

    assert cli.main(["--request", "request.json"]) == 4
    assert message in capsys.readouterr().err
