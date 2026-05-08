"""`fapiao serve` 子命令集成测试。"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest
from typer.testing import CliRunner

from fapiao_pdf import pipeline
import fapiao_pdf.cli as cli_mod

try:
    runner = CliRunner(mix_stderr=False)
except TypeError:
    runner = CliRunner()


@dataclass
class _FakeWebConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    retain_minutes: int = 60
    max_upload_mb: int = 200
    max_files: int = 200
    max_single_file_mb: int = 50


def _validate(config: _FakeWebConfig) -> _FakeWebConfig:
    if not 1 <= config.port <= 65535:
        raise ValueError(f"port 越界：{config.port}（应在 1..65535）")
    if config.retain_minutes < 1:
        raise ValueError(f"retain_minutes 必须 ≥ 1，当前 {config.retain_minutes}")
    return config


def _install_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    validate=_validate,
    create_app=lambda cfg: ("fake-app", cfg),
    uvicorn_run=lambda *_a, **_kw: None,
) -> None:
    monkeypatch.setattr(cli_mod, "WebConfig", _FakeWebConfig)
    monkeypatch.setattr(cli_mod, "validate_web_config", validate)
    monkeypatch.setattr(
        cli_mod,
        "web_app_mod",
        types.SimpleNamespace(create_app=create_app),
    )
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=uvicorn_run))


def test_serve_without_web_extras_exits_with_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_mod, "web_app_mod", None)
    monkeypatch.setattr(cli_mod, "WebConfig", None)
    monkeypatch.setattr(cli_mod, "validate_web_config", None)

    result = runner.invoke(cli_mod.app, ["serve"])

    assert result.exit_code == 2
    assert "Web 模式依赖未安装" in result.stderr
    assert "pip install -e .[web]" in result.stderr


def test_serve_warns_for_non_loopback_host_and_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ocr_calls: list[bool] = []
    run_calls: list[tuple[object, dict[str, object]]] = []

    def _fake_ocr(*, allow_download: bool) -> None:
        ocr_calls.append(allow_download)

    def _fake_run(app_obj: object, **kwargs: object) -> None:
        run_calls.append((app_obj, kwargs))

    _install_stubs(monkeypatch, uvicorn_run=_fake_run)
    monkeypatch.setattr(pipeline, "ensure_ocr_ready", _fake_ocr)

    result = runner.invoke(cli_mod.app, ["serve", "--host", "0.0.0.0"])

    assert result.exit_code == 0, result.stderr
    assert "安全警告" in result.stderr
    assert ocr_calls == [False]
    assert run_calls and run_calls[0][1]["workers"] == 1


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_serve_loopback_hosts_skip_security_warning(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    _install_stubs(monkeypatch)
    monkeypatch.setattr(pipeline, "ensure_ocr_ready", lambda **_: None)

    result = runner.invoke(cli_mod.app, ["serve", "--host", host])

    assert result.exit_code == 0, result.stderr
    assert "安全警告" not in result.stderr


def test_serve_ocr_model_missing_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stubs(monkeypatch)

    def _missing(*, allow_download: bool) -> None:
        raise pipeline.OcrModelMissingError("model not found")

    monkeypatch.setattr(pipeline, "ensure_ocr_ready", _missing)

    result = runner.invoke(cli_mod.app, ["serve"])

    assert result.exit_code == 2
    assert "OCR 模型未就绪" in result.stderr
    assert "fapiao init" in result.stderr


def test_serve_forwards_validated_config_to_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validated: list[_FakeWebConfig] = []
    created: list[_FakeWebConfig] = []
    runs: list[tuple[object, dict[str, object]]] = []

    def _capture_validate(cfg: _FakeWebConfig) -> _FakeWebConfig:
        validated.append(cfg)
        return cfg

    def _capture_create(cfg: _FakeWebConfig) -> str:
        created.append(cfg)
        return "fake-app"

    def _capture_run(app_obj: object, **kwargs: object) -> None:
        runs.append((app_obj, kwargs))

    _install_stubs(
        monkeypatch,
        validate=_capture_validate,
        create_app=_capture_create,
        uvicorn_run=_capture_run,
    )
    monkeypatch.setattr(pipeline, "ensure_ocr_ready", lambda **_: None)

    result = runner.invoke(
        cli_mod.app,
        [
            "serve",
            "--host", "localhost",
            "--port", "9001",
            "--retain-minutes", "30",
            "--max-upload-mb", "123",
            "--max-files", "45",
            "--max-single-file-mb", "12",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert len(validated) == 1
    cfg = validated[0]
    assert (cfg.host, cfg.port, cfg.retain_minutes) == ("localhost", 9001, 30)
    assert (cfg.max_upload_mb, cfg.max_files, cfg.max_single_file_mb) == (123, 45, 12)
    assert created == validated
    assert runs == [
        (
            "fake-app",
            {"host": "localhost", "port": 9001, "workers": 1, "log_config": None},
        )
    ]


def test_serve_rejects_invalid_port(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stubs(monkeypatch)
    monkeypatch.setattr(pipeline, "ensure_ocr_ready", lambda **_: None)

    result = runner.invoke(cli_mod.app, ["serve", "--port", "70000"])

    assert result.exit_code == 2
    assert "port 越界" in result.stderr


def test_serve_rejects_zero_retain_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stubs(monkeypatch)
    monkeypatch.setattr(pipeline, "ensure_ocr_ready", lambda **_: None)

    result = runner.invoke(cli_mod.app, ["serve", "--retain-minutes", "0"])

    assert result.exit_code == 2
    assert "retain_minutes" in result.stderr


def test_serve_fatal_run_error_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stubs(monkeypatch)

    def _fatal(*, allow_download: bool) -> None:
        raise pipeline.FatalRunError("disk crash")

    monkeypatch.setattr(pipeline, "ensure_ocr_ready", _fatal)

    result = runner.invoke(cli_mod.app, ["serve"])

    assert result.exit_code == 2
    assert "致命错误" in result.stderr

@pytest.mark.parametrize("host", ["LOCALHOST", "Localhost", "127.0.0.1"])
def test_serve_loopback_check_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    _install_stubs(monkeypatch)
    monkeypatch.setattr(pipeline, "ensure_ocr_ready", lambda **_: None)

    result = runner.invoke(cli_mod.app, ["serve", "--host", host])

    assert result.exit_code == 0, result.stderr
    assert "安全警告" not in result.stderr


def test_serve_uvicorn_missing_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "WebConfig", _FakeWebConfig)
    monkeypatch.setattr(cli_mod, "validate_web_config", _validate)
    monkeypatch.setattr(
        cli_mod,
        "web_app_mod",
        types.SimpleNamespace(create_app=lambda cfg: "fake-app"),
    )
    monkeypatch.setattr(pipeline, "ensure_ocr_ready", lambda **_: None)
    monkeypatch.delitem(sys.modules, "uvicorn", raising=False)

    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args, **kwargs):
        if name == "uvicorn":
            raise ImportError("uvicorn not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    result = runner.invoke(cli_mod.app, ["serve"])

    assert result.exit_code == 2
    assert "Web 模式依赖未安装" in result.stderr