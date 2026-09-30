"""Human-supervised X/Y seeks with local OCR and a +/-3 um position check."""

import argparse
from datetime import datetime
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import time
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from xy_readout import ReadoutError, TesseractReader, parse_xy


# Load the shared capture UI and position helpers without executing main().
# Its filename contains hyphens, so use a path-based import.
_spec = importlib.util.spec_from_file_location(
    "dwl_capture_helpers", Path(__file__).with_name("capture-xy-positions.py")
)
_capture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_capture)
TesterCancelled = _capture.CaptureCancelled
X_VALUES, Y_VALUES = _capture.X_VALUES, _capture.Y_VALUES
FIELDS, MOVE_ABSOLUTE = _capture.FIELDS, _capture.MOVE_ABSOLUTE
TOLERANCE = Decimal("3")
WAIT_SECONDS = 5


def check_position(raw_text, position, *, ocr_error=None):
    """Build a JSON-compatible result; missing/ambiguous text always fails."""
    result = {
        "commanded_x": position[0], "commanded_y": position[1],
        "raw_text": raw_text, "actual_x": None, "actual_y": None,
        "error_x": None, "error_y": None, "passed": False, "reason": "",
    }
    if ocr_error is not None:
        result["reason"] = f"OCR failed: {ocr_error}"
        return result
    try:
        actual_x, actual_y = parse_xy(raw_text)
    except ReadoutError as exc:
        result["reason"] = f"Could not parse X/Y: {exc}"
        return result
    dx = actual_x - Decimal(str(position[0]))
    dy = actual_y - Decimal(str(position[1]))
    passed = abs(dx) <= TOLERANCE and abs(dy) <= TOLERANCE
    result.update(
        actual_x=str(actual_x), actual_y=str(actual_y),
        error_x=str(dx), error_y=str(dy), passed=passed,
        reason="Both axes are within +/-3 um." if passed else "At least one axis is outside +/-3 um.",
    )
    return result


def format_result(result):
    status = "PASS" if result["passed"] else "FAIL"
    parsed = (
        f"X={result['actual_x']}, Y={result['actual_y']} um"
        if result["actual_x"] is not None else "unavailable"
    )
    errors = (
        f"X={result['error_x']}, Y={result['error_y']} um"
        if result["error_x"] is not None else "unavailable"
    )
    return (
        f"Commanded: X={result['commanded_x']}, Y={result['commanded_y']} um\n"
        f"Read string: {result['raw_text']!r}\n"
        f"Parsed: {parsed}\nError (actual - commanded): {errors}\n"
        f"{status}: {result['reason']}"
    )


def run_tester(psl, ui, reader, output_dir, sleep=time.sleep):
    """Run the 12 supervised tests; injected dependencies allow offline checks."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "results.jsonl"
    if log_path.exists() or any(output_dir.glob("step-*.png")):
        raise FileExistsError(f"Use a fresh output directory; test results already exist in {output_dir}")

    ui.show_status("Resolve the layout's Actual X/Y boundary before testing begins")
    box = tuple(psl.layoutBounds("actual-xy"))
    _capture.validate_box(box, psl.layoutViewport())
    results = []
    for axis, values in (("x", X_VALUES), ("y", Y_VALUES)):
        for index, value in enumerate(values):
            position = (value, 0) if axis == "x" else (X_VALUES[-1], value)
            ui.show_status(f"Entering X={position[0]}, Y={position[1]} um")
            # Set both fields every time: old text or edits during a review must
            # not silently change the companion axis of the next test.
            for field, target in zip(("x", "y"), position):
                _capture.require_viewport(psl)
                psl.doubleClick(FIELDS[field])
                psl.sendkeys(str(target))

            if not ui.confirm_move(axis, index, position):
                raise TesterCancelled()
            ui.show_status(f"Moving to X={position[0]}, Y={position[1]} um; waiting 5 seconds")
            _capture.require_viewport(psl)
            psl.click(MOVE_ABSOLUTE)
            sleep(WAIT_SECONDS)

            ui.show_status("Reading the selected X/Y pixels")
            crop = psl.frameGrab("actual-xy")
            try:
                raw_text = reader(crop)
                result = check_position(raw_text, position)
            except ReadoutError as exc:
                result = check_position("", position, ocr_error=exc)
            result.update(axis=axis, index=index, box=list(box))

            image_path = output_dir / f"step-{len(results) + 1:02d}-{axis}{index}.png"
            with image_path.open("xb") as stream:
                crop.save(stream, format="PNG")
            result["image"] = image_path.name
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            results.append(result)
            psl.info(f"Test {len(results)}/12 ({axis.upper()} index {index})\n{format_result(result)}")

            # Acknowledging FAIL records it as FAIL and proceeds with testing.
            # The tester never focuses or starts an exposure.
            if not ui.review_result(crop, result):
                raise TesterCancelled()
    return results


class TesterUI(_capture.CaptureUI):
    def check_cancel(self):
        # Preserve Stop/close events queued during browser/OCR calls.
        self.root.update()
        if self.cancelled:
            raise TesterCancelled()

    def show_status(self, message):
        super().show_status(message)
        self.root.title("DWL X/Y tester")

    def confirm_move(self, axis, index, position):
        self.check_cancel()
        self.clear("Confirm before moving")
        label = "row" if axis == "x" else "column"
        ttk.Label(self.content, text=f"{label.capitalize()} {index}: confirm destination fields",
                  font=("Segoe UI", 14, "bold")).pack(pady=20)
        ttk.Label(self.content, text=f"X = {position[0]} um\nY = {position[1]} um",
                  font=("Segoe UI", 18)).pack(pady=15)
        ttk.Label(self.content, wraplength=740,
                  text="The coordinates have been typed. Inspect the destination fields "
                       "on the machine display, then confirm to click Move absolute.").pack(pady=15)
        ttk.Button(self.content, text="Confirm and move", command=lambda: self.choose(True)).pack(pady=10)
        ttk.Button(self.content, text="Stop", command=self.cancel).pack(pady=10)
        return self.wait_for_choice()

    def review_result(self, crop, result):
        self.check_cancel()
        status = "PASS" if result["passed"] else "FAIL"
        self.clear(f"X/Y test result: {status}")
        self.root.geometry(f"850x{min(690, self.root.winfo_screenheight() - 100)}")
        ttk.Label(self.content, text=status, font=("Segoe UI", 20, "bold"),
                  foreground="#16733b" if result["passed"] else "#b00020").pack(pady=(4, 8))
        factor = min(4.0, 740 / crop.width, 150 / crop.height)
        self.photo = ImageTk.PhotoImage(crop.resize(
            (max(1, round(crop.width * factor)), max(1, round(crop.height * factor))),
            Image.Resampling.NEAREST,
        ))
        ttk.Label(self.content, image=self.photo).pack(pady=8)
        # Read-only text can scroll even when OCR returns unusually long output.
        import tkinter as tk
        text = tk.Text(self.content, height=9, wrap="word", font=("Consolas", 11))
        text.insert("1.0", format_result(result))
        text.configure(state="disabled")
        text.pack(fill="both", expand=True, pady=8)
        last = result["axis"] == "y" and result["index"] == 5
        ttk.Label(self.content, wraplength=740,
                  text="Accept acknowledges this result; a FAIL remains a FAIL. " +
                       ("This is the last test." if last else
                        "The next coordinates will be entered and await your move confirmation.")).pack(pady=8)
        buttons = ttk.Frame(self.content)
        buttons.pack(pady=8)
        ttk.Button(buttons, text="Accept and finish" if last else "Accept and continue",
                   command=lambda: self.choose(True)).pack(side="left", padx=8)
        ttk.Button(buttons, text="Stop", command=self.cancel).pack(side="left", padx=8)
        return self.wait_for_choice()


def main():
    parser = argparse.ArgumentParser(description=(
        "Test 6 X and 6 Y positions using local OCR and +/-3 um on BOTH axes. "
        "Start at Actual X=0, Y=0, head raised (Z=0), Global alignment panel open, "
        "and the browser displaying the chosen screen layout."
    ))
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:9222")
    parser.add_argument("--layout", type=Path, help="Layout folder; omit to choose a folder interactively")
    parser.add_argument("--tesseract-cmd", help="Path to tesseract.exe if not on PATH or in a standard install directory")
    parser.add_argument("--output-dir", type=Path, help="Fresh log/crop directory; defaults to runs/check-xy-<timestamp>")
    parser.add_argument("--image", type=Path, help="Read a saved crop only; no browser connection or movement")
    parser.add_argument("--target-x", type=Decimal, help="Commanded X in um for --image")
    parser.add_argument("--target-y", type=Decimal, help="Commanded Y in um for --image")
    args = parser.parse_args()
    if args.image and (args.target_x is None or args.target_y is None):
        parser.error("--image requires both --target-x and --target-y")
    if not args.image and (args.target_x is not None or args.target_y is not None):
        parser.error("--target-x and --target-y are only for --image")
    if any(target is not None and not target.is_finite() for target in (args.target_x, args.target_y)):
        parser.error("Targets must be finite numbers")

    # Check the OCR installation before connecting to the machine or typing.
    try:
        reader = TesseractReader(args.tesseract_cmd)
    except ReadoutError as exc:
        parser.exit(1, f"OCR setup failed: {exc}\n")
    if args.image:
        try:
            with Image.open(args.image) as image:
                raw = reader(image.convert("RGB"))
            result = check_position(raw, (args.target_x, args.target_y))
        except (ReadoutError, OSError) as exc:
            result = check_position("", (args.target_x, args.target_y), ocr_error=exc)
        print(format_result(result))
        return 0 if result["passed"] else 1

    import playwrightscriptlib as psl

    output_dir = args.output_dir or Path(__file__).resolve().parent / "runs" / (
        "check-xy-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    )
    ui = TesterUI()
    connected = False
    try:
        psl.connect(args.url, page_hint="remotedesktop.google.com")
        connected = True
        psl.loadLayout(args.layout)
        psl.clicksSettleTime(1)
        psl.pauseOnInfo(False)
        results = run_tester(psl, ui, reader, output_dir.resolve(), sleep=ui.wait_seconds)
        passed = sum(row["passed"] for row in results)
        summary = (f"Complete: {passed}/12 PASS, {12 - passed}/12 FAIL.\n"
                   f"Final commanded position: X=71222, Y=-71222 um.\n"
                   f"Results saved in {output_dir.resolve()}")
        print(summary)
        messagebox.showinfo("X/Y testing complete", summary, parent=ui.root)
        return 0 if passed == 12 else 1
    except (TesterCancelled, KeyboardInterrupt):
        print(f"Tester stopped; earlier results remain in {output_dir.resolve()}")
        return 2
    except Exception as exc:
        print(f"Tester failed: {exc}")
        messagebox.showerror("X/Y tester stopped", str(exc), parent=ui.root)
        return 1
    finally:
        try:
            if connected:
                psl.disconnect()
        finally:
            ui.close()


if __name__ == "__main__":
    raise SystemExit(main())
