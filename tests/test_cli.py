from pathlib import Path

import pytest
from typer.testing import CliRunner

from fapiao_pdf import __version__, pipeline
from fapiao_pdf.cli import app

try:
    runner = CliRunner(mix_stderr=False)
except TypeError:  # click 8.2+ 移除了该参数
    runner = CliRunner()


def test_version_prints_current_package_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


@pytest.mark.parametrize("pdf_dpi", [50, 400])
def test_merge_rejects_invalid_pdf_dpi(pdf_dpi: int, tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output = tmp_path / "out.pdf"

    result = runner.invoke(
        app, ["merge", str(input_dir), "-o", str(output), "--pdf-dpi", str(pdf_dpi)]
    )

    assert result.exit_code == 2
    assert "--pdf-dpi 必须在 100 到 300 之间" in result.stderr


def test_merge_rejects_invalid_workers(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output = tmp_path / "out.pdf"

    result = runner.invoke(
        app, ["merge", str(input_dir), "-o", str(output), "--workers", "5"]
    )

    assert result.exit_code == 2
    assert "--workers 必须在 1 到 4 之间" in result.stderr


def test_merge_requires_force_when_output_exists_in_argument_mode(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output = tmp_path / "out.pdf"
    output.write_text("existing", encoding="utf-8")

    result = runner.invoke(app, ["merge", str(input_dir), "-o", str(output)])

    assert result.exit_code == 2
    assert "请使用 --force 覆盖" in result.stderr


@pytest.mark.parametrize("answer", ["是", "y"])
def test_merge_accepts_interactive_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answer: str
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output = tmp_path / "out.pdf"
    output.write_text("existing", encoding="utf-8")

    def _fake_ocr(*, allow_download: bool) -> None:
        pass

    def _fake_run(
        merge_input_dir: Path,
        merge_output: Path,
        *,
        force: bool,
        pdf_dpi: int,
        workers: int,
    ) -> pipeline.RunStats:
        return pipeline.RunStats(
            processed=1, invoices=1, orders=0, ocr_failures=0, output_path=merge_output
        )

    monkeypatch.setattr(pipeline, "ensure_ocr_ready", _fake_ocr)
    monkeypatch.setattr(pipeline, "run_merge", _fake_run)

    result = runner.invoke(app, ["merge", str(input_dir)], input=f"{output}\n{answer}\n")

    assert result.exit_code == 0
    assert "是否覆盖" in result.stdout
    assert f"输出至 {output}" in result.stdout


def test_merge_overwrite_default_denial_preserves_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output = tmp_path / "out.pdf"
    output.write_text("existing", encoding="utf-8")

    monkeypatch.setattr(pipeline, "ensure_ocr_ready", lambda **_: None)

    result = runner.invoke(app, ["merge", str(input_dir)], input=f"{output}\n否\n")

    assert result.exit_code == 2
    assert "已取消覆盖" in result.stderr


def test_merge_prompts_for_missing_input_dir_in_chinese(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output = tmp_path / "out.pdf"

    monkeypatch.setattr(pipeline, "ensure_ocr_ready", lambda **_: None)
    monkeypatch.setattr(
        pipeline,
        "run_merge",
        lambda *_args, **_kwargs: pipeline.RunStats(
            processed=0, invoices=0, orders=0, ocr_failures=0, output_path=None
        ),
    )

    result = runner.invoke(app, ["merge", "-o", str(output)], input=f"{input_dir}\n")

    assert result.exit_code == 0
    assert "请输入输入目录路径" in result.stdout


def test_init_runs_real_ensure_when_dependencies_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "ensure_ocr_ready", lambda **_: None)
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "OCR 模型已就绪" in result.stdout


def test_help_lists_subcommands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "merge" in result.stdout
    assert "init" in result.stdout


def test_force_overwrites_existing_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output = tmp_path / "out.pdf"
    output.write_text("EXIST", encoding="utf-8")

    monkeypatch.setattr(pipeline, "ensure_ocr_ready", lambda **_: None)
    monkeypatch.setattr(
        pipeline,
        "run_merge",
        lambda *_a, **_k: pipeline.RunStats(
            processed=1, invoices=1, orders=0, ocr_failures=0, output_path=output
        ),
    )

    result = runner.invoke(
        app, ["merge", str(input_dir), "-o", str(output), "--force"]
    )
    assert result.exit_code == 0
    assert "共处理 1" in result.stdout
    assert f"输出至 {output}" in result.stdout
