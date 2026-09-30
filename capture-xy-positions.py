"""Collect operator-approved Actual X/Y pixel references; run with --help."""

import argparse
from pathlib import Path
import time
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageTk


# Keep these formulas and named controls consistent with real-dwl.py.
SPACING = 28489
X_VALUES = tuple(int((r - 2.5) * SPACING) for r in range(6))
Y_VALUES = tuple(int((c - 2.5) * -SPACING) for c in range(6))
FIELDS = {"x": "x-field", "y": "y-field"}
MOVE_ABSOLUTE = "move-absolute"
SETTLE_SECONDS = 5


class CaptureCancelled(Exception):
    """The operator stopped collection; issue no further commands."""


def capture_filename(axis, value, box):
    return f"capture-immediate-{axis}{value}um-{','.join(map(str, box))}.png"


def require_viewport(psl):
    psl.checkViewport()


def grab_viewport(psl):
    require_viewport(psl)
    shot = psl.viewportGrab()
    expected = psl.layoutViewport()
    if shot.size != expected:
        raise RuntimeError(f"Screenshot size {shot.size} differs from layout viewport {expected}.")
    return shot


def validate_box(box, viewport):
    if len(box) != 4 or any(type(n) is not int for n in box):
        raise ValueError("Select a rectangle in whole screenshot pixels.")
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 <= viewport[0] and 0 <= y1 < y2 <= viewport[1]):
        raise ValueError(f"Capture rectangle is outside the viewport: {box}")


def run_capture(psl, ui, output_dir, sleep=time.sleep):
    """Collect 14 approved images. Dependencies are injectable for offline tests."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(output_dir.glob("capture-immediate-[xy]*um-*.png"))
    if existing:
        raise FileExistsError(
            f"Existing X/Y captures in {output_dir}; use a fresh --output-dir. "
            f"First existing file: {existing[0].name}"
        )
    boxes = {}

    def collect(axis, value, position, select=False):
        ui.show_status(f"Capturing Actual {axis.upper()} for {value} um")
        if select:
            boxes[axis] = tuple(psl.layoutBounds(f"actual-{axis}"))
            validate_box(boxes[axis], psl.layoutViewport())
        shot = grab_viewport(psl)
        while True:
            box = boxes[axis]
            crop = shot.crop(box)
            action = ui.review_capture(crop, axis, value, box, position)
            if action == "accept":
                path = output_dir / capture_filename(axis, value, box)
                # Exclusive creation protects earlier references from replacement.
                with path.open("xb") as stream:
                    crop.save(stream, format="PNG")
                psl.info(f"Saved {path}")
                return
            if action == "reselect" and value == 0:
                boxes[axis] = tuple(psl.defineRegion(f"actual-{axis}"))
                validate_box(boxes[axis], psl.layoutViewport())
                shot = grab_viewport(psl)
                continue
            if action == "retry":
                ui.show_status("Waiting 5 seconds before recapturing; no new move")
                sleep(SETTLE_SECONDS)
                ui.show_status("Reading the screen again")
                shot = grab_viewport(psl)
                continue
            if action in ("stop", "reselect"):
                raise CaptureCancelled()
            raise ValueError(f"Unknown capture review action: {action!r}")

    collect("x", 0, (0, 0), select=True)
    collect("y", 0, (0, 0), select=True)

    # Actual zero does not imply that the editable destination fields contain zero.
    # Initialize Y explicitly so the X sweep keeps Y at zero.
    ui.show_status("Setting commanded Y to 0 for the X sweep")
    require_viewport(psl)
    psl.doubleClick(FIELDS["y"])
    psl.sendkeys("0")

    for axis, values in (("x", X_VALUES), ("y", Y_VALUES)):
        for value in values:
            position = (value, 0) if axis == "x" else (X_VALUES[-1], value)
            ui.show_status(f"Moving to X={position[0]}, Y={position[1]} um")
            require_viewport(psl)
            psl.doubleClick(FIELDS[axis])
            # sendkeys selects the complete existing text, including any minus sign.
            psl.sendkeys(str(value))
            ui.show_status(f"Seeking X={position[0]}, Y={position[1]} um; then waiting 5 seconds")
            require_viewport(psl)
            psl.click(MOVE_ABSOLUTE)
            # psl.wait() permits skipping; calibration always waits the full 5s.
            sleep(SETTLE_SECONDS)
            collect(axis, value, position)

    ui.show_status(
        f"Saved all 14 captures to {output_dir}. "
        f"Final commanded position: X={X_VALUES[-1]}, Y={Y_VALUES[-1]} um."
    )


class CaptureUI:
    """Local dialogs review captured pixels and control the supervised workflow."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("DWL Actual X/Y reference capture")
        self.cancelled = False
        self.result = None
        self.root.protocol("WM_DELETE_WINDOW", self.cancel)
        self.root.bind("<Escape>", lambda event: self.cancel())
        self.content = ttk.Frame(self.root, padding=12)
        self.content.pack(fill="both", expand=True)
        self.event = tk.BooleanVar(self.root, False)

    def cancel(self):
        self.cancelled = True
        self.event.set(True)

    def choose(self, result):
        self.result = result
        self.event.set(True)

    def clear(self, title, large=False):
        if self.cancelled:
            raise CaptureCancelled()
        for child in self.content.winfo_children():
            child.destroy()
        self.result = None
        self.root.title(title)
        width = min(1400 if large else 850, self.root.winfo_screenwidth() - 80)
        height = min(950 if large else 540, self.root.winfo_screenheight() - 100)
        self.root.geometry(f"{width}x{height}")
        self.root.lift()

    def wait_for_choice(self):
        self.event.set(False)
        self.root.wait_variable(self.event)
        if self.cancelled:
            raise CaptureCancelled()
        return self.result

    def show_status(self, message):
        # Handle Stop/close input queued during a wait before removing its widgets.
        self.root.update()
        if self.cancelled:
            raise CaptureCancelled()
        self.clear("DWL Actual X/Y reference capture")
        ttk.Label(self.content, text=message, wraplength=740).pack(pady=30)
        ttk.Label(
            self.content,
            text="Stop ends the script; a move already issued may still complete.",
        ).pack(pady=10)
        ttk.Button(self.content, text="Stop", command=self.cancel).pack(pady=10)
        self.root.update()
        if self.cancelled:
            raise CaptureCancelled()

    def wait_seconds(self, seconds):
        """Wait the full interval while allowing Stop/close to end collection."""
        self.root.update()
        if self.cancelled:
            raise CaptureCancelled()
        self.event.set(False)
        timer = self.root.after(round(seconds * 1000), lambda: self.event.set(True))
        try:
            self.root.wait_variable(self.event)
            if self.cancelled:
                raise CaptureCancelled()
        finally:
            self.root.after_cancel(timer)

    def review_capture(self, crop, axis, value, box, position):
        self.clear(f"Review Actual {axis.upper()}: expected {value} um")
        ttk.Label(
            self.content, text=f"Expected Actual {axis.upper()}: {value} um",
            font=("Segoe UI", 16, "bold"),
        ).pack(pady=(8, 6))
        ttk.Label(self.content, text=f"Expected position: X={position[0]}, Y={position[1]} um").pack()
        ttk.Label(self.content, text="Accept only if the complete displayed value matches the expected value.").pack(pady=8)
        factor = min(5.0, 740 / crop.width, 210 / crop.height)
        self.photo = ImageTk.PhotoImage(crop.resize(
            (max(1, round(crop.width * factor)), max(1, round(crop.height * factor))),
            Image.Resampling.NEAREST,
        ))
        ttk.Label(self.content, image=self.photo).pack(pady=12)
        ttk.Label(self.content, text=capture_filename(axis, value, box), wraplength=740).pack()
        if axis == "x" and value == 0:
            next_step = "After saving, define or reuse the Actual Y boundary and review its zero image."
        elif axis == "y" and value == Y_VALUES[-1]:
            next_step = "After saving, collection is complete. There is no return move."
        else:
            next_step = "After saving, the script automatically issues the next move."
        ttk.Label(self.content, text=next_step, wraplength=740).pack(pady=12)
        buttons = ttk.Frame(self.content)
        buttons.pack(pady=8)
        for label, action in (("Accept and save", "accept"), ("Wait 5s and recapture", "retry")):
            ttk.Button(buttons, text=label, command=lambda a=action: self.choose(a)).pack(side="left", padx=4)
        if value == 0:
            ttk.Button(buttons, text="Reselect rectangle", command=lambda: self.choose("reselect")).pack(side="left", padx=4)
        ttk.Button(buttons, text="Stop", command=self.cancel).pack(side="left", padx=4)
        return self.wait_for_choice()

    def close(self):
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=(
        "Capture Actual X/Y reference pixels with operator review. Start at Actual "
        "X=0, Y=0 with the head raised (Z=0), the Global alignment panel open, and "
        "the browser displaying the chosen screen layout."
    ))
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:9222", help="Chrome remote-debugging URL")
    parser.add_argument("--layout", type=Path, help="Layout folder; omit to choose a folder interactively")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent,
                        help="Capture directory (default: next to this script); existing X/Y captures are never overwritten")
    args = parser.parse_args()

    # Importing the module or running --help never connects to the machine.
    import playwrightscriptlib as psl

    ui = CaptureUI()
    connected = False
    try:
        psl.logging(True)
        psl.connect(args.url, page_hint="remotedesktop.google.com")
        connected = True
        psl.loadLayout(args.layout)
        psl.clicksSettleTime(1)
        psl.pauseOnInfo(False)
        run_capture(psl, ui, args.output_dir.resolve(), sleep=ui.wait_seconds)
        messagebox.showinfo("Capture complete", "Saved all 14 X/Y reference images.\n"
                            "Final commanded position: X=71222, Y=-71222 um.", parent=ui.root)
        return 0
    except (CaptureCancelled, KeyboardInterrupt):
        print("Capture stopped. Accepted images remain saved. No further moves will be issued.")
        return 2
    except Exception as exc:
        print(f"Capture failed: {exc}")
        messagebox.showerror("Capture stopped", str(exc), parent=ui.root)
        return 1
    finally:
        if connected:
            psl.disconnect()
        ui.close()


if __name__ == "__main__":
    raise SystemExit(main())
