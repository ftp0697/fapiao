"""输入扫描。"""

from pathlib import Path

_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".pdf"})


def _is_supported_file(file_path: Path) -> bool:
    return file_path.suffix.lower() in _SUPPORTED_EXTENSIONS


def _sort_key(base_dir: Path, file_path: Path) -> tuple[str, str]:
    relative_path: Path = file_path.relative_to(base_dir)
    return relative_path.as_posix().lower(), file_path.name


def scan_directory(path: Path) -> list[Path]:
    files, _warnings = scan_directory_with_warnings(path)
    return files


def scan_directory_with_warnings(path: Path) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    warnings: list[str] = []

    for entry in path.rglob("*"):
        try:
            if entry.is_symlink():
                warnings.append(f"跳过符号链接: {entry}")
                continue
            if not entry.is_file():
                if entry.exists() and not entry.is_dir():
                    warnings.append(f"跳过非普通文件: {entry}")
                continue
            if _is_supported_file(entry):
                files.append(entry)
        except OSError:
            warnings.append(f"跳过非普通文件: {entry}")

    files.sort(key=lambda f: _sort_key(path, f))
    return files, warnings
