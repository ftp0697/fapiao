"""命令行入口。"""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from fapiao_pdf import __version__
from fapiao_pdf import pipeline

_STDERR: bool = True
_OVERWRITE_ACCEPTED: frozenset[str] = frozenset({"y", "yes", "是", "确认"})

app: typer.Typer = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="用于合并发票与订单票据 PDF 的命令行工具。",
)


def _normalize_answer(value: str) -> str:
    return value.strip().casefold()


def _confirm_overwrite(path: Path) -> bool:
    answer: str = typer.prompt(
        f"输出文件已存在：{path}。是否覆盖？",
        default="否",
        show_default=False,
    )
    return _normalize_answer(answer) in _OVERWRITE_ACCEPTED


def _prompt_input_dir() -> Path:
    return Path(typer.prompt("请输入输入目录路径").strip()).expanduser()


def _prompt_output_path() -> Path:
    return Path(typer.prompt("请输入输出 PDF 路径").strip()).expanduser()


def _show_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit(code=0)


def _validate_pdf_dpi(pdf_dpi: int) -> int:
    if 100 <= pdf_dpi <= 300:
        return pdf_dpi
    typer.echo("参数错误：--pdf-dpi 必须在 100 到 300 之间。", err=_STDERR)
    raise typer.Exit(code=2)


def _validate_workers(workers: int) -> int:
    if 1 <= workers <= 4:
        return workers
    typer.echo("参数错误：--workers 必须在 1 到 4 之间。", err=_STDERR)
    raise typer.Exit(code=2)


def _resolve_merge_paths(
    input_dir: Path | None, output: Path | None
) -> tuple[Path, Path, bool]:
    argument_mode: bool = input_dir is not None and output is not None
    resolved_input: Path = (
        input_dir.expanduser() if input_dir is not None else _prompt_input_dir()
    )
    resolved_output: Path = (
        output.expanduser() if output is not None else _prompt_output_path()
    )
    return resolved_input, resolved_output, argument_mode


def _validate_input_dir(input_dir: Path) -> Path:
    if input_dir.exists() and input_dir.is_dir():
        return input_dir
    typer.echo(f"致命错误：输入目录不存在或不可访问：{input_dir}", err=_STDERR)
    raise typer.Exit(code=2)


def _resolve_force(argument_mode: bool, output: Path, force: bool) -> bool:
    if not output.exists():
        return force
    if argument_mode:
        if force:
            return True
        typer.echo(
            f"致命错误：输出文件已存在，请使用 --force 覆盖：{output}",
            err=_STDERR,
        )
        raise typer.Exit(code=2)
    if _confirm_overwrite(output):
        return True
    typer.echo("已取消覆盖现有输出文件。", err=_STDERR)
    raise typer.Exit(code=2)


def _print_summary(stats: pipeline.RunStats) -> None:
    output_path: Path | None = stats.output_path
    rendered: str = str(output_path) if output_path is not None else ""
    typer.echo(
        f"共处理 {stats.processed} 张，"
        f"发票 {stats.invoices}，"
        f"订单 {stats.orders}，"
        f"OCR 失败 {stats.ocr_failures}，"
        f"输出至 {rendered}"
    )


def _run_with_keyboard_interrupt(handler: Callable[[], None]) -> None:
    try:
        handler()
    except KeyboardInterrupt:
        raise typer.Exit(code=130)


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", "-V",
            callback=_show_version,
            is_eager=True,
            help="显示当前版本并退出。",
        ),
    ] = False,
) -> None:
    pass


@app.command("init")
def init_command() -> None:
    """初始化或校验 OCR 模型。"""

    def _execute() -> None:
        try:
            pipeline.ensure_ocr_ready(allow_download=True)
        except pipeline.OcrModelMissingError as exc:
            typer.echo(f"OCR 模型初始化失败：{exc}", err=_STDERR)
            raise typer.Exit(code=2)
        except pipeline.FatalRunError as exc:
            typer.echo(f"OCR 模型初始化失败：{exc}", err=_STDERR)
            raise typer.Exit(code=2)
        typer.echo("OCR 模型已就绪。")

    _run_with_keyboard_interrupt(_execute)


@app.command("merge")
def merge_command(
    input_dir: Annotated[
        Path | None,
        typer.Argument(help="输入目录，递归扫描其中的图片与 PDF 文件。"),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="输出 PDF 文件路径。"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="覆盖已存在的输出文件。"),
    ] = False,
    pdf_dpi: Annotated[
        int,
        typer.Option(
            "--pdf-dpi",
            help="PDF 渲染 DPI，默认 200，范围 100 到 300。",
        ),
    ] = 200,
    workers: Annotated[
        int,
        typer.Option("--workers", help="并行工作线程数，默认 1，最大 4。"),
    ] = 1,
) -> None:
    """合并输入目录中的票据为 PDF。"""

    def _execute() -> None:
        validated_dpi: int = _validate_pdf_dpi(pdf_dpi)
        validated_workers: int = _validate_workers(workers)
        resolved_input, resolved_output, argument_mode = _resolve_merge_paths(
            input_dir, output
        )
        _validate_input_dir(resolved_input)
        resolved_force: bool = _resolve_force(argument_mode, resolved_output, force)

        try:
            pipeline.ensure_ocr_ready(allow_download=False)
        except pipeline.OcrModelMissingError as exc:
            typer.echo(f"OCR 模型未就绪，请先运行 `fapiao init`：{exc}", err=_STDERR)
            raise typer.Exit(code=2)
        except pipeline.FatalRunError as exc:
            typer.echo(f"致命错误：{exc}", err=_STDERR)
            raise typer.Exit(code=2)

        try:
            stats: pipeline.RunStats = pipeline.run_merge(
                resolved_input,
                resolved_output,
                force=resolved_force,
                pdf_dpi=validated_dpi,
                workers=validated_workers,
            )
        except pipeline.NoProcessableInputError as exc:
            typer.echo(f"未发现可处理的输入：{exc}", err=_STDERR)
            raise typer.Exit(code=1)
        except pipeline.OcrModelMissingError as exc:
            typer.echo(f"OCR 模型未就绪，请先运行 `fapiao init`：{exc}", err=_STDERR)
            raise typer.Exit(code=2)
        except pipeline.FatalRunError as exc:
            typer.echo(f"致命错误：{exc}", err=_STDERR)
            raise typer.Exit(code=2)

        _print_summary(stats)

    _run_with_keyboard_interrupt(_execute)


if __name__ == "__main__":
    app()
