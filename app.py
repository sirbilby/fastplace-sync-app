#!/usr/bin/env python3
"""
app-v1.py
======
Part 2 of the "fast-place sync" toolkit.

A Tkinter GUI that:
  1. Lets you pick a source video, a clicks.txt log, and an output path.
  2. Scans for the red calibration sync flash.
  3. Provides a live preview interface with audio waveform visualization to
     fine-tune pre-roll timing using left/right arrow controls.
  4. Generates a Final Cut Pro XML (.fcpxml) timeline.
"""

import math
import xml.etree.ElementTree as ET
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from xml.dom import minidom

import cv2
import numpy as np
from PIL import Image, ImageTk
import av

# --------------------------------------------------------------------------
# Configuration & Constants
# --------------------------------------------------------------------------
FRAME_DURATIONS = {
    "30": (1, 30),
    "59.94": (1001, 60000),
    "60": (1, 60),
}

SYNC_SCAN_SECONDS = 10.0
SYNC_REGION_PX = 100
SYNC_RED_RATIO_THRESHOLD = 0.5
STANDARD_FORMAT_HEIGHTS = {720, 1080, 1440, 2160}


def format_resource_name(width: int, height: int, fps_key: str) -> str:
    if height in STANDARD_FORMAT_HEIGHTS:
        return f"FFVideoFormat{height}p{fps_key.replace('.', '')}"
    return f"Custom {width}x{height}p{fps_key}"


def rational_from_frames(frame_count: int, fps_key: str) -> str:
    num, den = FRAME_DURATIONS[fps_key]
    total_num = frame_count * num
    total_den = den
    if total_num == 0:
        return "0s"
    g = math.gcd(total_num, total_den)
    return f"{total_num // g}/{total_den // g}s"


def parse_click_log(path: Path):
    timestamps = []
    warnings = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                timestamps.append(float(line))
            except ValueError:
                warnings.append(f"Line {lineno}: could not parse {line!r} - skipped.")
    return timestamps, warnings


def detect_sync_frame(video_path: Path, fps: float,
                      max_seconds: float = SYNC_SCAN_SECONDS,
                      region_px: int = SYNC_REGION_PX,
                      red_ratio_threshold: float = SYNC_RED_RATIO_THRESHOLD):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {video_path}")

    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width <= 0 or height <= 0:
            raise RuntimeError(f"Video reports invalid dimensions: {width}x{height}")
        region_w = min(region_px, width)
        region_h = min(region_px, height)

        max_frames = max(1, int(round(fps * max_seconds)))
        frame_index = 0
        while frame_index < max_frames:
            ok, frame = cap.read()
            if not ok:
                break

            region = frame[0:region_h, 0:region_w]
            b = region[:, :, 0].astype(np.int16)
            g = region[:, :, 1].astype(np.int16)
            r = region[:, :, 2].astype(np.int16)
            red_mask = (r > 170) & (g < 90) & (b < 90)
            red_ratio = float(np.count_nonzero(red_mask)) / red_mask.size

            if red_ratio >= red_ratio_threshold:
                return frame_index, frame_index / fps

            frame_index += 1

        return None
    finally:
        cap.release()


def extract_audio_around_frame(video_path: Path, target_time: float, window_sec: float = 0.5):
    """Extract audio PCM samples surrounding target_time using PyAV."""
    try:
        container = av.open(str(video_path))
        if not container.streams.audio:
            return None, 44100

        audio_stream = container.streams.audio[0]
        sample_rate = audio_stream.codec_context.sample_rate

        start_time = max(0.0, target_time - (window_sec / 2.0))
        end_time = target_time + (window_sec / 2.0)

        seek_pts = int(start_time / float(audio_stream.time_base))
        container.seek(seek_pts, stream=audio_stream)

        samples = []
        for frame in container.decode(audio=0):
            frame_time = float(frame.pts * audio_stream.time_base)
            if frame_time > end_time:
                break
            if frame_time + float(frame.samples / sample_rate) >= start_time:
                array = frame.to_ndarray()
                if array.ndim > 1:
                    array = np.mean(array, axis=0)
                samples.append(array)

        if not samples:
            return None, sample_rate

        return np.concatenate(samples), sample_rate
    except Exception:
        return None, 44100


def build_fcpxml(video_path: Path, fps_key: str, cut_duration_frames: int,
                 start_frames_list, video_width: int, video_height: int,
                 video_total_frames: int) -> str:
    frame_num, frame_den = FRAME_DURATIONS[fps_key]

    fcpxml_el = ET.Element("fcpxml", version="1.10")
    resources = ET.SubElement(fcpxml_el, "resources")

    ET.SubElement(resources, "format", {
        "id": "r1",
        "name": format_resource_name(video_width, video_height, fps_key),
        "frameDuration": f"{frame_num}/{frame_den}s",
        "width": str(video_width),
        "height": str(video_height),
    })

    video_uri = Path(video_path).resolve().as_uri()
    asset_el = ET.SubElement(resources, "asset", {
        "id": "r2",
        "name": Path(video_path).stem,
        "start": "0s",
        "duration": rational_from_frames(max(video_total_frames, 1), fps_key),
        "hasVideo": "1",
        "videoSources": "1",
        "hasAudio": "1",
        "audioSources": "1",
        "audioChannels": "2",
        "format": "r1",
    })
    ET.SubElement(asset_el, "media-rep", {
        "kind": "original-media",
        "src": video_uri,
    })

    library = ET.SubElement(fcpxml_el, "library")
    event = ET.SubElement(library, "event", name="Fast-Place Sync Event")
    project = ET.SubElement(event, "project", name="Fast-Place Sync Project")

    total_seq_frames = len(start_frames_list) * cut_duration_frames
    sequence = ET.SubElement(project, "sequence", {
        "format": "r1",
        "duration": rational_from_frames(total_seq_frames, fps_key),
        "tcStart": "0s",
        "tcFormat": "NDF",
    })
    spine = ET.SubElement(sequence, "spine")

    for i, start_frame in enumerate(start_frames_list):
        offset_frames = i * cut_duration_frames
        ET.SubElement(spine, "asset-clip", {
            "name": f"BlockPlace_{i + 1:03d}",
            "ref": "r2",
            "offset": rational_from_frames(offset_frames, fps_key),
            "start": rational_from_frames(start_frame, fps_key),
            "duration": rational_from_frames(cut_duration_frames, fps_key),
            "format": "r1",
        })

    rough = ET.tostring(fcpxml_el, encoding="unicode")
    pretty = minidom.parseString(rough).toprettyxml(indent="    ")
    pretty_body = "\n".join(
        line for line in pretty.splitlines() if not line.strip().startswith("<?xml")
    )
    header = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n\n'
    return header + pretty_body.strip() + "\n"


# ==========================================================================
# GUI Implementation
# ==========================================================================
class FastPlaceSyncApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Fast-Place Sync - Timeline Fine Tuner")
        self.geometry("900x750")
        self.minsize(800, 650)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", padding=6)
        style.configure("Header.TLabel", font=("Helvetica", 14, "bold"))
        style.configure("Status.TLabel", foreground="#555555")

        # State Variables
        self.video_path = tk.StringVar()
        self.log_path = tk.StringVar(value=str(Path.cwd() / "clicks.txt"))
        self.output_path = tk.StringVar()
        self.fps_var = tk.StringVar(value="60")
        self.cut_duration_var = tk.StringVar(value="4")
        self.preroll_var = tk.StringVar(value="1")
        self.status_var = tk.StringVar(value="Ready.")

        # Sync state cache
        self.sync_frame_idx = 0
        self.first_click_ts = 0.0
        self.cap = None

        self._build_ui()
        self.bind("<Left>", lambda e: self._adjust_preroll(-1))
        self.bind("<Right>", lambda e: self._adjust_preroll(1))

    def _build_ui(self):
        pad = {"padx": 10, "pady": 4}

        ttk.Label(self, text="Fast-Place Sync Preview & Alignment", style="Header.TLabel").pack(anchor="w", **pad)

        # File selector frame
        files_frame = ttk.LabelFrame(self, text="Files")
        files_frame.pack(fill="x", **pad)
        self._file_row(files_frame, "Video (.mov / .mp4):", self.video_path, self._pick_video)
        self._file_row(files_frame, "Click Log (clicks.txt):", self.log_path, self._pick_log)
        self._file_row(files_frame, "Output (.fcpxml):", self.output_path, self._pick_output)

        # Settings
        params_frame = ttk.LabelFrame(self, text="Parameters")
        params_frame.pack(fill="x", **pad)
        row = ttk.Frame(params_frame)
        row.pack(fill="x", padx=10, pady=4)

        ttk.Label(row, text="FPS:").grid(row=0, column=0, sticky="w", padx=(0, 5))
        ttk.Combobox(row, textvariable=self.fps_var, values=["30", "59.94", "60"], state="readonly", width=8).grid(
            row=0, column=1, padx=(0, 15))

        ttk.Label(row, text="Cut Length (frames):").grid(row=0, column=2, sticky="w", padx=(0, 5))
        ttk.Entry(row, textvariable=self.cut_duration_var, width=8).grid(row=0, column=3, padx=(0, 15))

        ttk.Label(row, text="Pre-Roll Offset (frames):").grid(row=0, column=4, sticky="w", padx=(0, 5))
        preroll_entry = ttk.Entry(row, textvariable=self.preroll_var, width=8)
        preroll_entry.grid(row=0, column=5)
        self.preroll_var.trace_add("write", lambda *args: self._update_previews())

        ttk.Button(row, text="Scan & Sync Preview", command=self._load_and_sync).grid(row=0, column=6, padx=(15, 0))

        # Preview Section
        preview_frame = ttk.LabelFrame(self, text="Sync Verification")
        preview_frame.pack(fill="both", expand=True, **pad)

        preview_frame.columnconfigure(0, weight=1)
        preview_frame.columnconfigure(1, weight=1)
        preview_frame.rowconfigure(0, weight=1)

        # Sync Frame View
        f1 = ttk.Frame(preview_frame)
        f1.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        ttk.Label(f1, text="Reference Red Flash (t=0)").pack(anchor="nw")
        self.canvas_sync = tk.Canvas(f1, bg="black")
        self.canvas_sync.pack(fill="both", expand=True)

        # Target Click Frame View
        f2 = ttk.Frame(preview_frame)
        f2.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        ttk.Label(f2, text="First Action Click Frame").pack(anchor="nw")
        self.canvas_click = tk.Canvas(f2, bg="black")
        self.canvas_click.pack(fill="both", expand=True)

        # Audio Waveform Panel
        wf_frame = ttk.Frame(preview_frame)
        wf_frame.grid(row=1, column=1, sticky="ew", padx=5, pady=(0, 5))

        self.waveform_canvas = tk.Canvas(wf_frame, height=60, bg="#1e1e1e")
        self.waveform_canvas.pack(fill="x", expand=True)

        # Nudge Controls
        controls = ttk.Frame(preview_frame)
        controls.grid(row=2, column=1, sticky="ew", padx=5, pady=2)
        ttk.Button(controls, text="◄ -1 Frame", command=lambda: self._adjust_preroll(-1)).pack(side="left", padx=2)
        ttk.Button(controls, text="+1 Frame ►", command=lambda: self._adjust_preroll(1)).pack(side="left", padx=2)
        ttk.Label(controls, text="(Or use Left/Right Arrow keys)").pack(side="left", padx=10)

        # Generation Footer
        self.generate_btn = ttk.Button(self, text="Generate FCPXML", command=self._on_generate)
        self.generate_btn.pack(pady=(4, 2))
        self.status_lbl = ttk.Label(self, textvariable=self.status_var, style="Status.TLabel")
        self.status_lbl.pack(anchor="w", padx=10, pady=(0, 5))

        self.bind("<Configure>", lambda e: self._redraw_canvas_frames())

    def _file_row(self, parent, label, var, command):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=6, pady=3)
        ttk.Label(row, text=label, width=20, anchor="w").pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True, padx=(4, 6))
        ttk.Button(row, text="Browse...", command=command).pack(side="left")

    def _pick_video(self):
        path = filedialog.askopenfilename(filetypes=[("Video files", "*.mov *.mp4"), ("All files", "*.*")])
        if path:
            self.video_path.set(path)

    def _pick_log(self):
        path = filedialog.askopenfilename(initialdir=str(Path.cwd()),
                                          filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            self.log_path.set(path)

    def _pick_output(self):
        path = filedialog.asksaveasfilename(defaultextension=".fcpxml", filetypes=[("Final Cut Pro XML", "*.fcpxml")])
        if path:
            self.output_path.set(path)

    def _adjust_preroll(self, delta: int):
        try:
            val = int(self.preroll_var.get())
            self.preroll_var.set(str(val + delta))
        except ValueError:
            self.preroll_var.set("0")

    def _load_and_sync(self):
        video = Path(self.video_path.get().strip())
        log = Path(self.log_path.get().strip())

        if not video.is_file() or not log.is_file():
            messagebox.showerror("Error", "Valid Video and Log paths must be set.")
            return

        fps = float(self.fps_var.get())
        sync_res = detect_sync_frame(video, fps)

        if sync_res is None:
            messagebox.showerror("Sync Failed", "Could not detect visual calibration flash.")
            return

        self.sync_frame_idx, _ = sync_res
        timestamps, _ = parse_click_log(log)

        if not timestamps:
            messagebox.showerror("Log Error", "No timestamps parsed from click log.")
            return

        self.first_click_ts = timestamps[0]
        if self.cap:
            self.cap.release()
        self.cap = cv2.VideoCapture(str(video))

        self._update_previews()

    def _get_frame(self, frame_number: int):
        if not self.cap:
            return None
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_number))
        ok, frame = self.cap.read()
        if ok:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return None

    def _update_previews(self):
        if not self.cap:
            return

        try:
            preroll = int(self.preroll_var.get())
        except ValueError:
            preroll = 0

        fps = float(self.fps_var.get())
        sync_offset_sec = self.sync_frame_idx / fps
        click_target_sec = self.first_click_ts + sync_offset_sec
        click_frame_idx = max(0, round(click_target_sec * fps) - preroll)

        # Retain references to images so Tkinter garbage collection doesn't erase them
        self.sync_img_data = self._get_frame(self.sync_frame_idx)
        self.click_img_data = self._get_frame(click_frame_idx)

        self._redraw_canvas_frames()
        self._render_waveform(click_target_sec - (preroll / fps))

    def _render_image_on_canvas(self, canvas: tk.Canvas, img_array):
        canvas.delete("all")
        if img_array is None:
            return

        cw = canvas.winfo_width()
        ch = canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            return

        h, w, _ = img_array.shape
        scale = min(cw / w, ch / h)
        nw, nh = int(w * scale), int(h * scale)

        img = Image.fromarray(img_array).resize((nw, nh), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image=img)

        # Store ref on canvas instance
        canvas.photo = photo
        canvas.create_image(cw // 2, ch // 2, image=photo, anchor="center")

    def _redraw_canvas_frames(self):
        if hasattr(self, "sync_img_data"):
            self._render_image_on_canvas(self.canvas_sync, self.sync_img_data)
        if hasattr(self, "click_img_data"):
            self._render_image_on_canvas(self.canvas_click, self.click_img_data)

    def _render_waveform(self, target_time: float):
        self.waveform_canvas.delete("all")
        w = self.waveform_canvas.winfo_width()
        h = self.waveform_canvas.winfo_height()

        samples, rate = extract_audio_around_frame(Path(self.video_path.get()), target_time, window_sec=0.4)

        # Center marker line
        self.waveform_canvas.create_line(w // 2, 0, w // 2, h, fill="#ff4444", width=2)

        if samples is None or len(samples) == 0:
            self.waveform_canvas.create_text(w // 2, h // 2, text="No Audio Data Available", fill="#888888")
            return

        # Normalize PCM amplitudes
        max_val = np.max(np.abs(samples)) or 1.0
        normalized = samples / max_val

        pts = []
        step = len(normalized) / float(w)
        mid_y = h / 2.0

        for x in range(w):
            idx = int(x * step)
            if idx < len(normalized):
                y = mid_y - (normalized[idx] * (h / 2.0) * 0.85)
                pts.append((x, y))

        for i in range(len(pts) - 1):
            self.waveform_canvas.create_line(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], fill="#00ffcc",
                                             width=1)

    def _validate_inputs(self):
        video = Path(self.video_path.get().strip())
        log = Path(self.log_path.get().strip())
        output = self.output_path.get().strip()

        if not video.is_file() or video.suffix.lower() not in (".mov", ".mp4"):
            messagebox.showerror("Missing file", "Please select a valid video file (.mov or .mp4).")
            return None
        if not log.is_file():
            messagebox.showerror("Missing file", "Please select a valid clicks.txt log file.")
            return None
        if not output:
            messagebox.showerror("Missing output", "Please specify output XML path.")
            return None

        fps_key = self.fps_var.get()
        try:
            cut_duration = int(self.cut_duration_var.get())
            preroll = int(self.preroll_var.get())
        except ValueError:
            messagebox.showerror("Invalid numerical entry", "Cut Duration and Pre-Roll must be integers.")
            return None

        return {
            "video": video,
            "log": log,
            "output": Path(output),
            "fps_key": fps_key,
            "fps": float(fps_key),
            "cut_duration": cut_duration,
            "preroll": preroll,
        }

    def _on_generate(self):
        params = self._validate_inputs()
        if params is None:
            return

        try:
            sync_result = detect_sync_frame(params["video"], params["fps"])
            if sync_result is None:
                messagebox.showerror("Sync flash not found", "Could not detect visual sync flash.")
                return

            sync_frame_index, sync_offset_seconds = sync_result
            timestamps, warnings_list = parse_click_log(params["log"])
            if not timestamps:
                messagebox.showerror("Empty log", "No valid timestamps parsed.")
                return

            fps = params["fps"]
            start_frames = []
            clamped_count = 0
            for ts in timestamps:
                adjusted_seconds = ts + sync_offset_seconds
                frame = round(adjusted_seconds * fps) - params["preroll"]
                if frame < 0:
                    frame = 0
                    clamped_count += 1
                start_frames.append(frame)

            cap = cv2.VideoCapture(str(params["video"]))
            video_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
            video_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
            reported_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fallback_total = max(start_frames) + params["cut_duration"] if start_frames else 0
            video_total_frames = reported_total if reported_total > 0 else fallback_total
            cap.release()

            xml_string = build_fcpxml(
                video_path=params["video"],
                fps_key=params["fps_key"],
                cut_duration_frames=params["cut_duration"],
                start_frames_list=start_frames,
                video_width=video_width,
                video_height=video_height,
                video_total_frames=video_total_frames,
            )

            params["output"].parent.mkdir(parents=True, exist_ok=True)
            params["output"].write_text(xml_string, encoding="utf-8")

            summary_lines = [
                f"Generated timeline with {len(start_frames)} clips.",
                f"Output saved to: {params['output']}",
            ]
            if clamped_count:
                summary_lines.append(f"Warning: {clamped_count} clips were clamped to frame 0.")
            messagebox.showinfo("Success", "\n".join(summary_lines))

        except Exception as exc:
            messagebox.showerror("Error", f"An error occurred during generation:\n{exc}")


def main():
    app = FastPlaceSyncApp()
    app.mainloop()


if __name__ == "__main__":
    main()