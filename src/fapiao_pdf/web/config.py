"""Web 服务配置：dataclass + 默认常量 + 校验。"""

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

DEFAULT_HOST: Final[str] = "127.0.0.1"
DEFAULT_PORT: Final[int] = 8000
DEFAULT_RETAIN_MINUTES: Final[int] = 60
DEFAULT_MAX_UPLOAD_MB: Final[int] = 200
DEFAULT_MAX_FILES: Final[int] = 200
DEFAULT_MAX_SINGLE_FILE_MB: Final[int] = 50
DEFAULT_HEARTBEAT_SECONDS: Final[int] = 15
DEFAULT_CLEANUP_INTERVAL_SECONDS: Final[int] = 300
DEFAULT_DOWNLOAD_GRACE_SECONDS: Final[int] = 60
DEFAULT_EXPIRED_PLACEHOLDER_HOURS: Final[int] = 24
DEFAULT_EXPIRED_PLACEHOLDER_MAX: Final[int] = 1024
DEFAULT_UPLOAD_CHUNK_SIZE: Final[int] = 1 << 20
DEFAULT_MAX_SSE_PER_TASK: Final[int] = 16
DEFAULT_SUBSCRIBER_QUEUE_MAXSIZE: Final[int] = 32

_TASK_ROOT_NAME: Final[str] = "fapiao-tasks"
_PORT_MIN: Final[int] = 1
_PORT_MAX: Final[int] = 65535


def resolve_task_root() -> Path:
    """返回 <tmp>/fapiao-tasks，确保目录存在（mkdir 副作用）。"""
    root = Path(tempfile.gettempdir()) / _TASK_ROOT_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass(slots=True)
class WebConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    retain_minutes: int = DEFAULT_RETAIN_MINUTES
    max_upload_mb: int = DEFAULT_MAX_UPLOAD_MB
    max_files: int = DEFAULT_MAX_FILES
    max_single_file_mb: int = DEFAULT_MAX_SINGLE_FILE_MB
    heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS
    cleanup_interval_seconds: int = DEFAULT_CLEANUP_INTERVAL_SECONDS
    download_grace_seconds: int = DEFAULT_DOWNLOAD_GRACE_SECONDS
    expired_placeholder_hours: int = DEFAULT_EXPIRED_PLACEHOLDER_HOURS
    expired_placeholder_max: int = DEFAULT_EXPIRED_PLACEHOLDER_MAX
    upload_chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE
    task_root: Path = field(default_factory=resolve_task_root)
    max_sse_subscribers_per_task: int = DEFAULT_MAX_SSE_PER_TASK
    subscriber_queue_maxsize: int = DEFAULT_SUBSCRIBER_QUEUE_MAXSIZE


_POSITIVE_INT_FIELDS: Final[tuple[str, ...]] = (
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
)


def validate_web_config(config: WebConfig) -> WebConfig:
    """校验数值范围，越界抛 ValueError；返回原对象方便链式调用。"""
    if not config.host.strip():
        raise ValueError("host 不能为空")
    if not _PORT_MIN <= config.port <= _PORT_MAX:
        raise ValueError(f"port 越界：{config.port}（应在 {_PORT_MIN}..{_PORT_MAX}）")
    for name in _POSITIVE_INT_FIELDS:
        value = getattr(config, name)
        if value < 1:
            raise ValueError(f"{name} 必须 ≥ 1，当前 {value}")
    return config
