from pathlib import Path

import pytest

from fapiao_pdf.web.config import (
    DEFAULT_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_DOWNLOAD_GRACE_SECONDS,
    DEFAULT_EXPIRED_PLACEHOLDER_HOURS,
    DEFAULT_EXPIRED_PLACEHOLDER_MAX,
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_HOST,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_SINGLE_FILE_MB,
    DEFAULT_MAX_SSE_PER_TASK,
    DEFAULT_MAX_UPLOAD_MB,
    DEFAULT_PORT,
    DEFAULT_RETAIN_MINUTES,
    DEFAULT_SUBSCRIBER_QUEUE_MAXSIZE,
    DEFAULT_UPLOAD_CHUNK_SIZE,
    WebConfig,
    resolve_task_root,
    validate_web_config,
)


def test_defaults_match_spec() -> None:
    cfg = WebConfig()
    assert cfg.host == DEFAULT_HOST == "127.0.0.1"
    assert cfg.port == DEFAULT_PORT == 8000
    assert cfg.retain_minutes == DEFAULT_RETAIN_MINUTES == 60
    assert cfg.max_upload_mb == DEFAULT_MAX_UPLOAD_MB == 200
    assert cfg.max_files == DEFAULT_MAX_FILES == 200
    assert cfg.max_single_file_mb == DEFAULT_MAX_SINGLE_FILE_MB == 50
    assert cfg.heartbeat_seconds == DEFAULT_HEARTBEAT_SECONDS == 15
    assert cfg.cleanup_interval_seconds == DEFAULT_CLEANUP_INTERVAL_SECONDS == 300
    assert cfg.download_grace_seconds == DEFAULT_DOWNLOAD_GRACE_SECONDS == 60
    assert cfg.expired_placeholder_hours == DEFAULT_EXPIRED_PLACEHOLDER_HOURS == 24
    assert cfg.expired_placeholder_max == DEFAULT_EXPIRED_PLACEHOLDER_MAX == 1024
    assert cfg.upload_chunk_size == DEFAULT_UPLOAD_CHUNK_SIZE == 1 << 20
    assert cfg.max_sse_subscribers_per_task == DEFAULT_MAX_SSE_PER_TASK == 16
    assert cfg.subscriber_queue_maxsize == DEFAULT_SUBSCRIBER_QUEUE_MAXSIZE == 32
    assert isinstance(cfg.task_root, Path)
    assert cfg.task_root.is_dir()


def test_resolve_task_root_under_custom_tmp(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "fapiao_pdf.web.config.tempfile.gettempdir", lambda: str(tmp_path)
    )
    root = resolve_task_root()
    assert root == tmp_path / "fapiao-tasks"
    assert root.is_dir()
    # idempotent
    assert resolve_task_root() == root


def test_validate_returns_same_instance() -> None:
    cfg = WebConfig()
    assert validate_web_config(cfg) is cfg


@pytest.mark.parametrize("port", [0, -1, 65536, 100000])
def test_validate_rejects_port_out_of_range(port: int) -> None:
    cfg = WebConfig(port=port)
    with pytest.raises(ValueError, match="port"):
        validate_web_config(cfg)


@pytest.mark.parametrize(
    "field_name",
    [
        "retain_minutes",
        "max_upload_mb",
        "max_files",
        "max_single_file_mb",
        "heartbeat_seconds",
        "cleanup_interval_seconds",
        "download_grace_seconds",
        "expired_placeholder_hours",
        "expired_placeholder_max",
        "upload_chunk_size",
        "max_sse_subscribers_per_task",
        "subscriber_queue_maxsize",
    ],
)
def test_validate_rejects_non_positive(field_name: str) -> None:
    cfg = WebConfig(**{field_name: 0})
    with pytest.raises(ValueError, match=field_name):
        validate_web_config(cfg)


def test_validate_rejects_empty_host() -> None:
    cfg = WebConfig(host="")
    with pytest.raises(ValueError, match="host"):
        validate_web_config(cfg)


def test_validate_rejects_whitespace_host() -> None:
    cfg = WebConfig(host="   ")
    with pytest.raises(ValueError, match="host"):
        validate_web_config(cfg)


def test_validate_accepts_boundary_port() -> None:
    assert validate_web_config(WebConfig(port=1)).port == 1
    assert validate_web_config(WebConfig(port=65535)).port == 65535
