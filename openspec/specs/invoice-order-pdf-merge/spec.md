## ADDED Requirements

### Requirement: CLI initialization and merge commands
The system SHALL provide a Python 3.11+ command line application named `fapiao` with `init` and `merge` commands.

#### Scenario: Initialize OCR models
- **WHEN** the user runs `fapiao init`
- **THEN** the system SHALL initialize or verify local OCR model availability and print a Chinese success or actionable failure message

#### Scenario: Merge with arguments
- **WHEN** the user runs `fapiao merge <input_dir> -o <output.pdf>` with valid paths
- **THEN** the system SHALL process the input directory and write the merged PDF to the requested output path

#### Scenario: Merge interactively
- **WHEN** the user runs `fapiao merge` without required paths
- **THEN** the system SHALL prompt in Chinese for the input directory and output PDF path

#### Scenario: Existing output in argument mode
- **WHEN** the output PDF already exists and `--force` is not provided in argument mode
- **THEN** the system SHALL exit without overwriting the file and print a Chinese actionable error

#### Scenario: Existing output in interactive mode
- **WHEN** the output PDF already exists in interactive mode
- **THEN** the system SHALL ask for overwrite confirmation and default to not overwriting

### Requirement: Input scanning
The system SHALL recursively scan a user-provided directory for supported files and build a deterministic logical input list.

#### Scenario: Supported file discovery
- **WHEN** the input directory contains `.jpg`, `.jpeg`, `.png`, or `.pdf` files in any extension casing
- **THEN** the system SHALL include those files in deterministic relative-path order

#### Scenario: Unsupported files
- **WHEN** the input directory contains unsupported files
- **THEN** the system SHALL ignore them without treating them as processing failures

#### Scenario: No supported files
- **WHEN** no supported files are found
- **THEN** the system SHALL exit with code `1` and print `未发现可处理文件`

#### Scenario: Symbolic links and special files
- **WHEN** the scanner encounters a symbolic link or non-regular file
- **THEN** the system SHALL skip it, print a Chinese warning to stderr, and continue scanning

### Requirement: PDF page expansion
The system SHALL expand supported PDF inputs into independent logical page images.

#### Scenario: PDF page rendering
- **WHEN** a supported PDF contains renderable pages
- **THEN** the system SHALL render each page at `--pdf-dpi` DPI and assign each page a stable display key containing a one-based page index

#### Scenario: PDF DPI bounds
- **WHEN** the user provides `--pdf-dpi`
- **THEN** the system SHALL accept integer values from `100` through `300`, defaulting to `200`

#### Scenario: Encrypted PDF
- **WHEN** the system detects a password-protected or encrypted PDF
- **THEN** the system SHALL skip the PDF, print `加密PDF不支持，已跳过：<路径>` to stderr, and continue

#### Scenario: Failed PDF rendering
- **WHEN** a PDF file or PDF page cannot be rendered
- **THEN** the system SHALL skip the failed file or page, print a Chinese warning to stderr, and continue processing other inputs

### Requirement: Multi-receipt page splitting
The system SHALL perform conservative automatic splitting for logical page images that appear to contain multiple receipts.

#### Scenario: High-confidence multiple regions
- **WHEN** OpenCV detects at least two non-overlapping receipt regions that satisfy configured area, dimension, aspect-ratio, and IoU thresholds
- **THEN** the system SHALL crop each region with padding and treat each crop as an independent logical document

#### Scenario: Low-confidence splitting
- **WHEN** splitting produces fewer than two accepted regions, overlapping candidates, or an exception
- **THEN** the system SHALL treat the original page image as one logical document and print a Chinese warning to stderr

#### Scenario: Split ordering
- **WHEN** multiple crops are accepted from one page
- **THEN** the system SHALL order crops from top to bottom and from left to right within the same row

#### Scenario: Split identity
- **WHEN** a crop is produced from an image or PDF page
- **THEN** the system SHALL assign a stable display key containing the original relative path, optional page index, and one-based crop index

### Requirement: Image normalization and orientation
The system SHALL normalize image inputs before OCR and rendering.

#### Scenario: EXIF orientation
- **WHEN** an image contains EXIF Orientation metadata
- **THEN** the system SHALL apply the EXIF orientation before OCR and layout

#### Scenario: OCR orientation
- **WHEN** OCR returns trusted orientation information
- **THEN** the system SHALL rotate the logical document image accordingly before layout

#### Scenario: Orientation unavailable
- **WHEN** OCR orientation information is unavailable or untrusted
- **THEN** the system SHALL keep the current orientation, print a Chinese warning, and continue

#### Scenario: Corrupt image
- **WHEN** an image cannot be decoded
- **THEN** the system SHALL skip that image, print a Chinese warning to stderr, and continue processing other inputs

### Requirement: OCR processing
The system SHALL use local PaddleOCR for text extraction and SHALL NOT upload user images to any remote service during `merge`.

#### Scenario: Local OCR success
- **WHEN** OCR recognizes text for a logical document
- **THEN** the system SHALL pass the OCR text stream to type classification and date parsing without logging the text content

#### Scenario: OCR model missing during merge
- **WHEN** required OCR models are unavailable during `fapiao merge`
- **THEN** the system SHALL fail with exit code `2` and instruct the user in Chinese to run `fapiao init`

#### Scenario: OCR exception
- **WHEN** OCR raises an exception for a logical document
- **THEN** the system SHALL count one OCR failure, degrade that document to type `order` with no date, print a warning containing only the display key, and continue

#### Scenario: Empty OCR text
- **WHEN** OCR completes but produces no text
- **THEN** the system SHALL count one OCR failure, degrade that document to type `order` with no date, print a warning containing only the display key, and continue

### Requirement: Receipt type classification
The system SHALL classify each retained logical document as `invoice` or `order` using OCR keyword rules.

#### Scenario: Invoice keyword precedence
- **WHEN** OCR text contains any invoice keyword including `发票`, `税额`, `价税合计`, `发票号码`, or `发票代码`
- **THEN** the system SHALL classify the document as `invoice` even if order keywords are also present

#### Scenario: Order keyword match
- **WHEN** OCR text contains order keywords including `订单`, `订单号`, `订单编号`, `商品清单`, or `收货地址` and no invoice keyword is present
- **THEN** the system SHALL classify the document as `order`

#### Scenario: Unknown type fallback
- **WHEN** OCR text contains neither invoice nor order keywords
- **THEN** the system SHALL classify the document as `order` and print `类型识别失败，按订单处理：<文件路径>` to stderr

### Requirement: Date extraction
The system SHALL extract a document date from OCR text using deterministic supported formats.

#### Scenario: Supported date formats
- **WHEN** OCR text contains `YYYY-MM-DD`, `YYYY/MM/DD`, `YYYY.MM.DD`, or `YYYY年MM月DD日` with one- or two-digit month/day
- **THEN** the system SHALL parse valid calendar dates from those candidates

#### Scenario: Multiple date candidates
- **WHEN** OCR text contains multiple valid supported dates
- **THEN** the system SHALL choose the valid date with the earliest position in the OCR text stream

#### Scenario: Invalid calendar date
- **WHEN** OCR text contains date-like text that is not a real calendar date
- **THEN** the system SHALL reject that candidate and continue checking other candidates

#### Scenario: No valid date
- **WHEN** no valid supported date is found
- **THEN** the system SHALL leave the document date empty and print a Chinese warning to stderr

### Requirement: Ordering
The system SHALL produce a deterministic output ordering by type group and per-group date rules.

#### Scenario: Type group order
- **WHEN** the system orders processed documents
- **THEN** all `invoice` documents SHALL appear before all `order` documents

#### Scenario: Dated documents within group
- **WHEN** documents in the same type group have valid dates
- **THEN** the system SHALL order them by date ascending

#### Scenario: Undated documents within group
- **WHEN** documents in the same type group lack valid dates
- **THEN** the system SHALL place them after all dated documents in that group and order them by display key

#### Scenario: Equal dates
- **WHEN** documents in the same type group have the same valid date
- **THEN** the system SHALL order them by display key

#### Scenario: Deterministic shuffle resistance
- **WHEN** the same logical documents are provided in any input enumeration order
- **THEN** the system SHALL produce the same output ordering

### Requirement: A4 layout planning
The system SHALL plan printable A4 portrait pages with fixed margins and no mixed-type page content.

#### Scenario: A4 page size
- **WHEN** the system creates a layout page
- **THEN** the page SHALL be A4 portrait with dimensions 210mm by 297mm

#### Scenario: Invoice page capacity
- **WHEN** invoice documents are laid out
- **THEN** each invoice page SHALL contain one or two invoice images only

#### Scenario: Invoice page geometry
- **WHEN** an invoice page contains two invoice images
- **THEN** the images SHALL be placed in vertical cells separated by a 5mm gap with 10mm page margins

#### Scenario: Order page capacity
- **WHEN** order documents are laid out
- **THEN** each order page SHALL contain exactly one order image

#### Scenario: No mixed pages
- **WHEN** pages are planned from ordered documents
- **THEN** no page SHALL contain both invoice and order documents

#### Scenario: Aspect ratio preservation
- **WHEN** an image is placed into a layout cell
- **THEN** the system SHALL scale it proportionally, center it, and keep it within the cell bounds

### Requirement: PDF rendering and output safety
The system SHALL render the planned layout into a valid PDF without leaving partial final output on failure.

#### Scenario: Successful render
- **WHEN** layout rendering completes successfully
- **THEN** the system SHALL atomically replace or create the final output PDF from a same-directory temporary file

#### Scenario: Render failure
- **WHEN** rendering fails before completion
- **THEN** the system SHALL not replace the final output path and SHALL clean up temporary files where possible

#### Scenario: User interruption
- **WHEN** the user interrupts processing with Ctrl+C or SIGTERM
- **THEN** the system SHALL clean up temporary files, avoid replacing the final output, and exit with code `130`

### Requirement: Progress, warnings, statistics, and exit codes
The system SHALL provide Chinese user-facing progress, warning, summary, and exit code behavior suitable for batch use.

#### Scenario: Progress output
- **WHEN** processing is running in a TTY
- **THEN** the system SHALL show progress including processed count, total count, and current stage

#### Scenario: Non-TTY progress output
- **WHEN** stdout is not a TTY
- **THEN** the system SHALL degrade progress to simple text messages

#### Scenario: Warning privacy
- **WHEN** the system prints warnings or errors
- **THEN** it SHALL NOT print OCR text, amounts, tax IDs, identity information, or other sensitive receipt content

#### Scenario: Final summary
- **WHEN** processing completes and a PDF is generated
- **THEN** stdout SHALL include `共处理 N 张，发票 X，订单 Y，OCR 失败 Z，输出至 <path>`

#### Scenario: Partial failures with output
- **WHEN** at least one document is successfully rendered and at least one input fails
- **THEN** the system SHALL write the PDF, print warnings, include failure counts in the summary, and exit with code `0`

#### Scenario: All processable documents fail
- **WHEN** supported files are found but no document can be retained for rendering
- **THEN** the system SHALL not create an empty PDF and SHALL exit with code `1`

### Requirement: Property-based verification
The implementation SHALL include property-based tests for core deterministic behavior and layout invariants.

#### Scenario: PBT for classification and date parsing
- **WHEN** generated OCR text contains keyword and date combinations
- **THEN** property tests SHALL verify invoice precedence, unknown fallback, valid date parsing, invalid date rejection, and earliest-position selection

#### Scenario: PBT for ordering
- **WHEN** generated processed document lists are shuffled
- **THEN** property tests SHALL verify output order remains deterministic and respects type group/date/display-key rules

#### Scenario: PBT for splitting and layout
- **WHEN** generated geometry, region, and document-count inputs are provided
- **THEN** property tests SHALL verify split fallback rules, invoice page capacity, order page capacity, no mixed pages, A4 geometry, bounds containment, and aspect ratio preservation

#### Scenario: PBT for output safety model
- **WHEN** generated render failure points or interruption points are simulated
- **THEN** property tests SHALL verify the final output path is not replaced before successful completion
