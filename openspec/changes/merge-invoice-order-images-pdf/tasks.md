## 1. Project scaffold

- [ ] 1.1 Create Python 3.11+ `pyproject.toml` with `src/` layout, console script `fapiao`, and runtime/dev dependency groups
- [ ] 1.2 Create package modules for CLI, models, scanner, PDF pages, image IO, splitter, OCR, classifier, date parser, ordering, layout, renderer, and stats
- [ ] 1.3 Configure pytest and Hypothesis test layout under `tests/`
- [ ] 1.4 Add a single source of truth for package version used by `fapiao --version`

## 2. CLI behavior

- [ ] 2.1 Implement Typer root app with `init` and `merge` commands
- [ ] 2.2 Implement `fapiao init` to initialize or verify local PaddleOCR model readiness with Chinese output
- [ ] 2.3 Implement `fapiao merge [input_dir] -o <output.pdf> --force --pdf-dpi --workers` argument validation
- [ ] 2.4 Implement interactive `merge` prompts for missing input/output paths
- [ ] 2.5 Implement output overwrite rules for argument and interactive modes
- [ ] 2.6 Implement exit code mapping for success, no processable input, fatal error, and interruption

## 3. Domain models and scanning

- [ ] 3.1 Define dataclasses for logical input, split document, OCR result, processed document, layout slot, layout page, warning, and run stats
- [ ] 3.2 Implement recursive pathlib scanner with case-insensitive `.jpg`, `.jpeg`, `.png`, `.pdf` filtering
- [ ] 3.3 Skip symlinks and non-regular files with stderr warnings
- [ ] 3.4 Produce deterministic relative-path ordering for discovered inputs
- [ ] 3.5 Add unit tests for scanner extension handling, deterministic ordering, and skipped special files

## 4. PDF expansion and image normalization

- [ ] 4.1 Implement PyMuPDF PDF opening, encrypted PDF detection, and page count discovery
- [ ] 4.2 Render PDF pages at configurable `--pdf-dpi` with default `200` and valid range `100..300`
- [ ] 4.3 Assign stable display keys for PDF pages using one-based page indexes
- [ ] 4.4 Implement Pillow image loading with eager `load()`, safe file handle closing, RGB conversion, and EXIF transpose
- [ ] 4.5 Handle corrupt images and failed PDF pages by skipping with Chinese stderr warnings
- [ ] 4.6 Add tests for PDF page identity, DPI validation, corrupt image handling, and encrypted PDF skip behavior

## 5. Multi-receipt splitting

- [ ] 5.1 Implement OpenCV-based splitter using grayscale, denoise, thresholding, contour detection, and axis-aligned bounding boxes
- [ ] 5.2 Apply acceptance thresholds for region count, area ratio, min dimensions, aspect ratio, and IoU
- [ ] 5.3 Crop accepted regions with 2% padding and assign stable one-based crop display keys
- [ ] 5.4 Sort accepted crops top-to-bottom and left-to-right within rows
- [ ] 5.5 Fall back to the full page with a Chinese warning when split confidence is low or splitting fails
- [ ] 5.6 Add unit and property-based tests for split acceptance, fallback, non-overlap, and crop ordering

## 6. OCR adapter and orientation

- [ ] 6.1 Define OCR engine interface returning text stream, optional orientation data, and failure metadata
- [ ] 6.2 Implement PaddleOCR adapter using local models and no remote image upload during `merge`
- [ ] 6.3 Detect missing OCR model readiness during `merge` and fail with code `2` plus `fapiao init` guidance
- [ ] 6.4 Apply OCR orientation correction only when orientation data is trusted; otherwise warn and continue
- [ ] 6.5 Count OCR exceptions and empty OCR text as OCR failures and degrade documents to order/no-date
- [ ] 6.6 Add fake OCR tests for success, exception, empty text, and orientation fallback paths

## 7. Classification, date parsing, and ordering

- [ ] 7.1 Implement invoice-first keyword classification constants and pure classification function
- [ ] 7.2 Implement unknown-type fallback to order with required Chinese warning text
- [ ] 7.3 Implement date parser for `YYYY-MM-DD`, `YYYY/MM/DD`, `YYYY.MM.DD`, and `YYYY年MM月DD日`
- [ ] 7.4 Validate calendar dates and select the earliest valid candidate by OCR text-stream position
- [ ] 7.5 Implement ordering: invoice group first, order group second, dated items before undated items, display-key tie breaker
- [ ] 7.6 Add unit tests for keyword precedence, fallback, date formats, invalid dates, multiple candidates, and ordering rules
- [ ] 7.7 Add Hypothesis tests for classification determinism, date parser totality, invalid-date rejection, and sort determinism under shuffled inputs

## 8. Layout planner

- [ ] 8.1 Implement pure A4 portrait layout planner using 210mm × 297mm page geometry
- [ ] 8.2 Implement 10mm page margins and 5mm gap between invoice cells
- [ ] 8.3 Plan invoice pages with one or two invoice images only
- [ ] 8.4 Plan order pages with exactly one order image only
- [ ] 8.5 Compute proportional image placement centered within each cell without overflow
- [ ] 8.6 Add unit and Hypothesis tests for A4 dimensions, page capacity, no mixed pages, bounds containment, and aspect ratio preservation

## 9. PDF rendering and output safety

- [ ] 9.1 Implement ReportLab renderer from layout pages to same-directory temporary PDF
- [ ] 9.2 Draw normalized images into computed layout slots with preserved aspect ratio
- [ ] 9.3 Atomically replace or create the final output only after successful PDF close
- [ ] 9.4 Clean up temporary files on render failure, Ctrl+C, and SIGTERM
- [ ] 9.5 Add tests using fake render failure points to verify final output is not replaced before success
- [ ] 9.6 Add smoke test verifying generated PDF page count and A4 page dimensions

## 10. Progress, warnings, and summary

- [ ] 10.1 Implement TTY progress with processed count, total count, and current stage
- [ ] 10.2 Implement non-TTY simple text progress fallback
- [ ] 10.3 Route warnings to stderr and ensure OCR text and sensitive receipt fields are never logged
- [ ] 10.4 Implement final stdout summary `共处理 N 张，发票 X，订单 Y，OCR 失败 Z，输出至 <path>`
- [ ] 10.5 Add tests for partial failure exit code `0`, all-failed exit code `1`, fatal exit code `2`, and interruption exit code `130`

## 11. End-to-end validation

- [ ] 11.1 Add integration test with fake OCR and generated sample images covering invoices, orders, same-date ties, and undated documents
- [ ] 11.2 Add integration test covering PDF input expansion and multi-receipt split fallback behavior
- [ ] 11.3 Add CLI tests for help, version, interactive prompts, overwrite confirmation, and `--force`
- [ ] 11.4 Run full pytest suite and fix failures without weakening requirements
- [ ] 11.5 Verify `openspec status --change "merge-invoice-order-images-pdf" --json` reports tasks as ready for apply
