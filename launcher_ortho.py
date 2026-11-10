import os
import json
import ctypes
from math import ceil, floor, hypot
import queue
import stat
import struct
import subprocess
import sys
import threading
import time
import zlib
from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import dataclass
from itertools import count
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pipeline_ortho import ACCURACY_DOWNSCALE, CONTROL_FILE, PAUSE_MARKER, STOP_MARKER, find_ffmpeg
from preview_pixels import PhotoWriter, Raster, scale_regions


METASHAPE = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Agisoft" / "Metashape Pro" / "metashape.exe"
PIPELINE = Path(__file__).resolve().with_name("pipeline_ortho.py")
IMAGE_TYPES = {".tif", ".tiff", ".png"}
VIDEO_TYPES = {
    ".3g2", ".3gp", ".asf", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov",
    ".mp4", ".mpeg", ".mpg", ".mts", ".mxf", ".ts", ".vob", ".webm", ".wmv",
}
INPUT_TYPES = IMAGE_TYPES | VIDEO_TYPES
FRAME_TYPES = IMAGE_TYPES | {".jpg", ".jpeg"}
VIDEO_STAGES = (
    "Konversi video ke frame",
    "Buat proyek Metashape",
    "Cocokkan foto",
    "Selaraskan kamera",
    "Buat model",
    "Buat orthomosaic",
    "Ekspor orthomosaic TIFF",
    "Auto Tone adaptif",
)
IMAGE_STAGES = ("Auto Tone adaptif",)
NO_WINDOW = subprocess.CREATE_NO_WINDOW
BACKGROUND = "#f2f5f7"
SURFACE = "#ffffff"
INK = "#14292e"
MUTED = "#4d6369"
LINE = "#d9e3e5"
PANEL_EDGE = "#bfd1cf"
BRAND_DARK = "#123733"
CMP_NAVY = "#102c43"
CMP_MINT = "#a2efda"
TRACK = "#d5e5e2"
ACCENT = "#086557"
SOFT = "#e9f4f1"
VIEWER = "#132324"
ZOOM_LEVELS = (50, 75, 100, 125, 150, 200, 300, 400, 500, 650, 800, 1000)
ZOOM_MARGIN = 120
ZOOM_DURATION = 0.28
PANEL_RADIUS = 28
RUN_BADGES = ("StartBadge.TLabel", "WorkBadge.TLabel", "LateBadge.TLabel")


@dataclass
class QueueEntry:
    source: Path
    status: str = "Menunggu"
    output: Path = None
    error: str = ""
    frames: tuple = ()

    @property
    def enhance_only(self):
        return not self.frames and self.source.suffix.lower() in IMAGE_TYPES

    @property
    def title(self):
        return f"{len(self.frames)} foto frame · {self.source.parent.name}" if self.frames else self.source.name


@dataclass(frozen=True)
class ZoomRegion:
    full: tuple
    source_size: tuple
    crop: tuple
    bounds: tuple

    def covers(self, full, visible):
        left, top, right, bottom = self.bounds
        return (self.full == full and left <= visible[0] and top <= visible[1]
                and right >= visible[2] and bottom >= visible[3])


def visible_bounds(full, viewport, pan):
    origin = tuple((view - size) // 2 + offset for size, view, offset in zip(full, viewport, pan))
    first = tuple(max(0, min(size - 1, -start)) for size, start in zip(full, origin))
    last = tuple(max(start + 1, min(size, view - offset))
                 for start, size, view, offset in zip(first, full, viewport, origin))
    return (*first, *last)


def zoom_region(full, source_size, visible, margin=ZOOM_MARGIN):
    starts, ends = [], []
    for index, (size, source) in enumerate(zip(full, source_size)):
        starts.append(max(0, floor((visible[index] - margin) * source / size)))
        ends.append(min(source, ceil((visible[index + 2] + margin) * source / size)))
    tile_start = tuple(round(start * size / source) for start, size, source in zip(starts, full, source_size))
    tile_end = tuple(round(end * size / source) for end, size, source in zip(ends, full, source_size))
    return ZoomRegion(full, source_size, (*starts, *ends), (*tile_start, *tile_end))


def create_output(parent, name, kind):
    for number in count(1):
        suffix = "" if number == 1 else f"_{number}"
        output = parent / f"{name}_{kind}{suffix}"
        try:
            output.mkdir()
            return output
        except FileExistsError:
            continue


def start_job(source, output, accuracy="Medium", frames=()):
    if not frames and source.suffix.lower() in IMAGE_TYPES:
        command = [sys.executable, "-B", str(PIPELINE), "--enhance"]
    else:
        command = [str(METASHAPE), "-r", str(PIPELINE), "--accuracy", accuracy]
        if frames:
            command.append("--frames")
            source = output / "daftar-frame.json"
            source.write_text(json.dumps(list(map(str, frames))), encoding="utf-8")
    with (output / "metashape.log").open("xb") as log:
        try:
            return subprocess.Popen(
                [*command, str(source), str(output)], stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, creationflags=NO_WINDOW,
            )
        except OSError as exc:
            log.write(f"Gagal memulai proses: {exc}\n".encode("utf-8"))
            raise


def render_previews(sources, results):
    try:
        ffmpeg = find_ffmpeg()
        previews = []
        for source in sources:
            preview = subprocess.run(
                [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-filter_threads", "1",
                 "-i", str(source), "-vf",
                 "format=rgba,scale=w='min(iw,4096)':h='min(ih,4096)':force_original_aspect_ratio=decrease:flags=lanczos",
                 "-frames:v", "1", "-c:v", "bmp", "-pix_fmt", "bgra",
                 "-f", "image2pipe", "pipe:1"],
                capture_output=True, check=True, timeout=60, creationflags=NO_WINDOW,
            )
            previews.append(Raster.from_bmp(preview.stdout))
        results.put(previews)
    except (RuntimeError, OSError, ValueError, struct.error, subprocess.SubprocessError):
        results.put(None)


def render_zoom(sources, results, region, moving, enhanced_only):
    try:
        if enhanced_only:
            after, = scale_regions(sources[1:], region, moving)
            results.put((None, after))
        else:
            results.put(scale_regions(sources, region, moving))
    except (RuntimeError, OSError, AttributeError, ctypes.ArgumentError):
        results.put(None)


def timeline_status(lines):
    durations = []
    percent = None
    marker = ""
    for line in lines:
        if line.startswith("MULAI  "):
            marker, percent = line, None
        elif line.startswith("PROGRES  "):
            value = line.removeprefix("PROGRES  ")
            if value.isdigit() and 0 <= int(value) < 100:
                percent = int(value)
        elif line.startswith("SELESAI  "):
            marker, percent = line, None
            durations.append(line.rpartition(" (")[2].removesuffix(" detik)").replace(".", ","))
        elif line.startswith("GAGAL  "):
            marker = line
    return durations, percent, marker


def rounded_image(master, radius, fill, edge=None, mask=False):
    size = radius * 2 + 3
    inner = bytes.fromhex(fill[1:])
    outer = bytes.fromhex(edge[1:]) if edge else inner
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            dx = max(radius - x - 0.5, x + 0.5 - (size - radius), 0)
            dy = max(radius - y - 0.5, y + 0.5 - (size - radius), 0)
            distance = hypot(dx, dy) - radius
            coverage = max(0, min(1, 0.5 - distance))
            if mask:
                rows.extend((*inner, round(255 * (1 - coverage))))
            else:
                weight = max(0, min(1, -0.5 - distance)) if edge else 1
                color = [round(a + (b - a) * weight) for a, b in zip(outer, inner)]
                rows.extend((*color, round(255 * coverage)))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    return tk.PhotoImage(master=master, data=png)


def corner_tiles(master, image, radius):
    edge = image.width() - radius
    tiles = []
    for x, y in ((0, 0), (edge, 0), (edge, edge), (0, edge)):
        tile = tk.PhotoImage(master=master, width=radius, height=radius)
        tile.tk.call(str(tile), "copy", str(image), "-from", x, y, x + radius, y + radius)
        tiles.append(tile)
    return tiles


def rounded_panel(parent, corners, **layout):
    outer = ttk.Frame(parent, style="Surface.TFrame")
    outer.grid(**layout)
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(0, weight=1)
    radius = PANEL_RADIUS
    # Match the one-pixel stroke built into the corner tiles.
    for bottom in (0, 1):
        edge = tk.Frame(outer, bg=PANEL_EDGE, borderwidth=0, highlightthickness=0)
        edge.place(x=radius - 1, rely=bottom, anchor="sw" if bottom else "nw",
                   relwidth=1, width=2 - 2 * radius, height=1)
    for right in (0, 1):
        edge = tk.Frame(outer, bg=PANEL_EDGE, borderwidth=0, highlightthickness=0)
        edge.place(relx=right, y=radius - 1, anchor="ne" if right else "nw",
                   relheight=1, height=2 - 2 * radius, width=1)
    for image, (x, y, anchor) in zip(corners, ((0, 0, "nw"), (1, 0, "ne"), (1, 1, "se"), (0, 1, "sw"))):
        corner = tk.Label(outer, image=image, background=BACKGROUND, borderwidth=0, highlightthickness=0)
        corner.place(relx=x, rely=y, anchor=anchor, width=radius, height=radius, bordermode="outside")
    content = ttk.Frame(outer, style="Surface.TFrame")
    content.grid(sticky="nsew", padx=14, pady=10)
    return content


class StageBar(tk.Canvas):
    def __init__(self, parent, width=54, height=7):
        super().__init__(parent, height=height, width=width, background=SURFACE, highlightthickness=0)
        self.value = 0
        self.displayed = 0
        self.color = ACCENT
        self.offset = 0
        self.animation = None
        self.bind("<Configure>", self.draw)

    def update_progress(self, value, color):
        if (value, color) == (self.value, self.color):
            return
        previous = self.value
        self.value, self.color = value, color
        self.cancel_animation()
        if value is None:
            if previous is not None:
                self.offset = 0
            self.animate()
        elif value > self.displayed:
            self.tween(self.displayed, time.perf_counter())
        else:
            self.displayed = value
            self.draw()

    def cancel_animation(self):
        if self.animation is not None:
            self.after_cancel(self.animation)
            self.animation = None

    def destroy(self):
        self.cancel_animation()
        super().destroy()

    def tween(self, start, started):
        elapsed = min(1, (time.perf_counter() - started) / 0.15)
        self.displayed = start + (self.value - start) * elapsed * (2 - elapsed)
        self.draw()
        self.animation = self.after(16, self.tween, start, started) if elapsed < 1 else None

    def animate(self):
        self.offset = (self.offset + 1.5) % 125
        self.draw()
        self.animation = self.after(17, self.animate)

    def draw(self, _event=None):
        self.delete("bar")
        height = self.winfo_height()
        start_x, end_x = height / 2, self.winfo_width() - height / 2
        if end_x <= start_x:
            return
        self.create_line(start_x, height / 2, end_x, height / 2,
                         fill=TRACK, width=height, capstyle="round", tags="bar")
        start, end = (self.offset - 25, self.offset) if self.value is None else (0, self.displayed)
        if end > 0 and start < 100:
            span = (end_x - start_x) / 100
            self.create_line(
                start_x + max(0, start) * span, height / 2,
                start_x + min(100, end) * span, height / 2,
                fill=self.color, width=height, capstyle="round", tags="bar",
            )


class PreviewPane(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, background=VIEWER, highlightthickness=0, cursor="fleur")
        self.viewport = None
        self.photo = None
        self.picture = self.create_image(0, 0, anchor="nw")

    def hide(self):
        if self.viewport is not None:
            self.place_forget()
            self.viewport = None
        if self.photo is not None:
            self.itemconfigure(self.picture, image="")
            self.photo = None

    def show(self, photo, left, top, start, end, width, height, corners, divider=False):
        if end <= start:
            self.hide()
            return
        viewport = (start, end, width, height, divider)
        if self.viewport != viewport:
            self.viewport = viewport
            self.place(x=start, y=0, width=end - start, height=height)
            self.delete("grid", "corner", "divider")
            for x in range((-start) % 24, end - start, 24):
                self.create_line(x, 0, x, height, fill="#223638", tags="grid")
            for y in range(0, height, 24):
                self.create_line(0, y, end - start, y, fill="#223638", tags="grid")
            self.tag_lower("grid")
            if divider:
                self.create_line(0, 0, 0, height, fill="#e7fff4", tags="divider")
            edges = ((-start, 0), (width - start, 0), (width - start, height), (-start, height))
            for edge, corner, anchor in zip(edges, corners, ("nw", "ne", "se", "sw")):
                self.create_image(*edge, image=corner, anchor=anchor, tags="corner")
        if self.photo is not photo:
            self.itemconfigure(self.picture, image=photo)
            self.photo = photo
        self.coords(self.picture, left - start, top)


class App:
    def __init__(self, root):
        self.root = root
        self.source = None
        self.output = None
        self.process = None
        self.batch = []
        self.batch_index = None
        self.job_command = "run"
        self.keep_destination = False
        self.queue_window = None
        self.preview_job = None
        self.preview_pending = None
        self.viewed_result = None
        self.enhanced_view = False
        self.zoom_job = None
        self.zoom_timer = None
        self.resize_timer = None
        self.draw_timer = None
        self.zoom_revision = 0
        self.zoom_cache = None
        self.zoom_region = None
        self.draw_size = None
        self.master_previews = None
        self.zoom_percent = self.display_zoom = 100
        self.zoom_motion = None
        self.pan = (0, 0)
        self.pan_start = None
        self.photos = []
        self.stages = VIDEO_STAGES
        self.first_stage = 1
        self.destination = tk.StringVar(value="Mengikuti folder file sumber.")
        self.destination_label = tk.StringVar(value=self.destination.get())
        self.destination.trace_add("write", self.show_destination)
        self.input_text = tk.StringVar(value="Belum ada file yang dipilih.")
        self.accuracy = tk.StringVar(value="Medium")
        self.queue_caption = tk.StringVar(value="Antrean")
        self.mode = tk.StringVar(value="Pilih video, foto frame, atau gambar untuk Auto Tone.")
        self.status = tk.StringVar(value="Menunggu input.")
        self.badge = tk.StringVar(value="Menunggu input")
        self.details = tk.StringVar()
        self.preview_status = tk.StringVar(value="Hasil akan muncul setelah proses selesai.")
        self.stage_count = tk.StringVar(value="0%")
        self.timeline_label = tk.StringVar(value="Pratinjau alur video")
        self.iterations = []
        self.iteration_index = 0
        self.follow_iterations = True
        self.active_iteration = 0
        self.engine_offset = 0
        self.engine_stage = -1
        self.engine_lines = {}
        root.title("CMP — Coral Marine Photogrammetry")
        root.configure(bg=BACKGROUND)
        root.minsize(1060, 760)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.configure_styles()

        header = ttk.Frame(root, style="Surface.TFrame", padding=(20, 8))
        header.pack(fill="x")
        ttk.Label(header, text="CMP", style="Mark.TLabel", padding=(12, 6)).pack(side="left", padx=(0, 12))
        brand = ttk.Frame(header, style="Surface.TFrame")
        brand.pack(side="left")
        wordmark = ttk.Frame(brand, style="Surface.TFrame")
        wordmark.pack(anchor="w")
        ttk.Label(wordmark, text="Coral Marine", style="Title.TLabel").pack(side="left", padx=(0, 6))
        ttk.Label(wordmark, text="Photogrammetry", style="TitleAccent.TLabel").pack(side="left")
        ttk.Label(brand, text="Fotogrametri bawah laut", style="Muted.TLabel").pack(anchor="w")
        logo = Path(__file__).with_name("logo-dinar.png")
        if logo.is_file():
            self.dinar_image = tk.PhotoImage(master=root, file=str(logo))
            ttk.Label(header, image=self.dinar_image, style="Dinar.TLabel", padding=(9, 5)).pack(
                side="right", padx=(12, 0),
            )
        self.badge_label = ttk.Label(header, textvariable=self.badge, style="Badge.TLabel", padding=(10, 4))
        self.badge_label.pack(side="right")

        footer = ttk.Frame(root, style="Surface.TFrame", padding=(20, 5))
        footer.pack(side="bottom", fill="x")
        ttk.Label(footer, text="Pemrosesan lokal · v1.24", style="Muted.TLabel").pack(side="left")
        ttk.Label(footer, text="Agisoft Metashape Pro + FFmpeg", style="Muted.TLabel").pack(side="right")
        body = ttk.Frame(root, style="Workspace.TFrame", padding=(16, 10))
        body.pack(fill="both", expand=True)
        for column in (1, 2):
            body.columnconfigure(column, weight=1, uniform="workspace")
        body.rowconfigure(0, weight=1)

        sidebar = self.sidebar = ttk.Frame(body, style="Workspace.TFrame")
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(1, weight=1)
        self.build_inputs(rounded_panel(sidebar, self.panel_corners, row=0, column=0, sticky="ew", pady=(0, 10)))
        self.build_timeline(rounded_panel(sidebar, self.panel_corners, row=1, column=0, sticky="nsew"))
        result = rounded_panel(body, self.panel_corners, row=0, column=1, sticky="nsew", padx=(0, 12))
        self.build_results(result)
        root.bind("<Map>", self.preview_mapped, add="+")
        root.bind("<Unmap>", self.preview_unmapped, add="+")
        self.build_iterations(rounded_panel(body, self.panel_corners, row=0, column=2, sticky="nsew"))
        self.clear_iterations()
        self.show_stages([])

    def show_destination(self, *_args):
        path = self.destination.get()
        self.destination_label.set(path if len(path) <= 43 else f"{path[:16]}…{path[-24:]}")

    def adjust_minimum(self):
        height = min(max(760, self.root.winfo_reqheight() + 32), self.root.winfo_screenheight() - 65)
        self.root.minsize(1060, height)

    def configure_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        self.style_images = []

        def image(radius, fill, edge=None):
            asset = rounded_image(self.root, radius, fill, edge)
            self.style_images.append(asset)
            return asset

        def surface(name, role, radius, fill, edge=None, states=()):
            element = f"Round.{name}"
            normal = image(radius, fill, edge)
            variants = [(*state, image(radius, background, border)) for state, background, border in states]
            style.element_create(element, "image", normal, *variants, border=radius, padding=0, sticky="nsew")
            style.layout(name, [(element, {"sticky": "nsew", "children": [
                (f"{role}.padding", {"sticky": "nsew", "children": [(f"{role}.label", {"sticky": "nsew"})]}),
            ]})])

        style.configure(".", font=("Segoe UI", 10), foreground=INK)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("Workspace.TFrame", background=BACKGROUND)
        panel = rounded_image(self.root, PANEL_RADIUS, SURFACE, PANEL_EDGE)
        self.panel_corners = corner_tiles(self.root, panel, PANEL_RADIUS)
        style.configure("TLabel", background=SURFACE)
        style.configure("Title.TLabel", foreground=CMP_NAVY, font=("Segoe UI", 17, "bold"))
        style.configure("TitleAccent.TLabel", foreground=ACCENT, font=("Segoe UI Semibold", 17))
        style.configure("Mark.TLabel", foreground=CMP_MINT, font=("Segoe UI Black", 16))
        style.configure("Heading.TLabel", font=("Segoe UI", 13, "bold"))
        style.configure("Field.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Muted.TLabel", foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Badge.TLabel", foreground=ACCENT, font=("Segoe UI", 9, "bold"))
        for badge in (*RUN_BADGES, "DoneBadge.TLabel", "ErrorBadge.TLabel", "WarningBadge.TLabel"):
            style.configure(badge, foreground=SURFACE, font=("Segoe UI", 9, "bold"))
        style.configure("Value.TLabel", foreground=MUTED, font=("Segoe UI", 9), padding=(8, 4))
        style.configure("Active.TLabel", foreground=ACCENT, font=("Segoe UI", 9, "bold"))
        style.configure("Error.TLabel", foreground="#a32629", font=("Segoe UI", 9, "bold"))
        style.configure("TButton", padding=(10, 3), background=SURFACE)
        neutral_backdrop = [(state, SURFACE) for state in ("disabled", "pressed", "active")]
        style.map("TButton", background=neutral_backdrop, foreground=[("disabled", "#627277")])
        style.configure("Primary.TButton", foreground=SURFACE)
        style.map("Primary.TButton", foreground=[("disabled", MUTED), ("!disabled", SURFACE)])
        style.configure("TSeparator", background=LINE)
        surface("TButton", "Button", 10, SURFACE, PANEL_EDGE, states=(
            (("disabled",), "#f7f9f9", LINE), (("pressed",), TRACK, ACCENT),
            (("focus",), SOFT, ACCENT), (("active",), SOFT, PANEL_EDGE),
        ))
        surface("Primary.TButton", "Button", 10, ACCENT, states=(
            (("disabled",), SOFT, PANEL_EDGE), (("pressed",), "#06463e", None),
            (("focus",), ACCENT, "#14292e"), (("active",), "#075347", None),
        ))
        surface("Badge.TLabel", "Label", 9, SOFT)
        surface("Mark.TLabel", "Label", 11, CMP_NAVY, "#28736f")
        surface("Dinar.TLabel", "Label", 11, BRAND_DARK)
        for badge, fill in zip(
            (*RUN_BADGES, "DoneBadge.TLabel", "ErrorBadge.TLabel", "WarningBadge.TLabel"),
            ("#265d92", "#1d647f", "#086557", "#087339", "#a32629", "#865515"),
        ):
            surface(badge, "Label", 9, fill)
        surface("Value.TLabel", "Label", 8, "#f4f8f7", LINE)
        style.configure("Compare.Horizontal.TScale", background=SURFACE)
        style.map("Compare.Horizontal.TScale", background=neutral_backdrop)
        style.element_create("Compare.trough", "image", image(3, TRACK), border=3, padding=0, sticky="ew")
        style.element_create(
            "Compare.slider", "image", image(7, ACCENT), ("disabled", image(7, "#9cb8b3")),
            ("focus", image(7, ACCENT, INK)), ("active", image(7, "#075347")), sticky="",
        )
        style.layout("Compare.Horizontal.TScale", [
            ("Compare.trough", {"sticky": "ew", "children": [("Compare.slider", {"side": "left", "sticky": ""})]}),
        ])

    @staticmethod
    def caption(parent, variable, wrap):
        return tk.Label(parent, textvariable=variable, wraplength=wrap, height=2, width=1,
                        anchor="nw", justify="left", bg=SURFACE, fg=MUTED, font=("Segoe UI", 9),
                        padx=0, pady=0, borderwidth=0, highlightthickness=0)

    def build_inputs(self, panel):
        panel.columnconfigure(0, minsize=260, weight=1)
        ttk.Label(panel, text="Siapkan input", style="Heading.TLabel").grid(sticky="w")
        self.caption(panel, self.mode, 260).grid(sticky="ew", pady=(3, 12))
        picker = ttk.Frame(panel, style="Surface.TFrame")
        picker.grid(sticky="ew")
        for column in range(2):
            picker.columnconfigure(column, weight=1, uniform="input")
        self.pick_button = ttk.Button(picker, text="Video…", command=self.pick_file)
        self.pick_frames_button = ttk.Button(picker, text="Foto Frames…", command=self.pick_frames)
        self.queue_button = ttk.Button(picker, textvariable=self.queue_caption, command=self.open_queue, state="disabled")
        self.pick_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.pick_frames_button.grid(row=0, column=1, sticky="ew")
        self.queue_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        ttk.Label(panel, textvariable=self.input_text, style="Value.TLabel", width=1).grid(
            sticky="ew", pady=(6, 16),
        )

        folder_row = ttk.Frame(panel, style="Surface.TFrame")
        folder_row.grid(sticky="ew")
        ttk.Label(folder_row, text="Folder hasil", style="Field.TLabel").pack(side="left")
        self.folder_button = ttk.Button(folder_row, text="Ubah…", command=self.pick_folder)
        self.folder_button.pack(side="right")
        ttk.Label(panel, textvariable=self.destination_label, style="Value.TLabel", width=1).grid(
            sticky="ew", pady=(6, 13),
        )
        self.accuracy_row = ttk.Frame(panel, style="Surface.TFrame")
        self.accuracy_row.grid(sticky="ew", pady=(0, 9))
        ttk.Label(self.accuracy_row, text="Akurasi pencocokan", style="Field.TLabel").pack(side="left")
        self.accuracy_button = ttk.Button(self.accuracy_row, text="Medium ▾", command=self.pick_accuracy)
        self.accuracy_button.pack(side="right")
        self.accuracy_menu = tk.Menu(self.root, tearoff=False)
        for level in ACCURACY_DOWNSCALE:
            self.accuracy_menu.add_radiobutton(
                label=level, variable=self.accuracy, value=level,
                command=lambda: self.accuracy_button.configure(text=f"{self.accuracy.get()} ▾"),
            )
        self.accuracy_row.grid_remove()

        controls = ttk.Frame(panel, style="Surface.TFrame")
        controls.grid(sticky="ew", pady=(0, 3))
        self.run_button = ttk.Button(controls, text="▶ Mulai", style="Primary.TButton", command=self.run, state="disabled")
        self.pause_button = ttk.Button(controls, text="⏸ Jeda", command=self.toggle_pause, state="disabled")
        self.stop_button = ttk.Button(controls, text="■ Berhenti", command=self.request_stop, state="disabled")
        self.reset_button = ttk.Button(controls, text="↺ Reset", command=self.reset_inputs, state="disabled")
        for index, button in enumerate((self.run_button, self.pause_button, self.stop_button, self.reset_button)):
            controls.columnconfigure(index % 2, weight=1, uniform="control")
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, 5) if index % 2 == 0 else 0, pady=(0, 4))

    def build_timeline(self, panel):
        panel.columnconfigure(0, minsize=296, weight=1)
        heading = ttk.Frame(panel, style="Surface.TFrame")
        heading.grid(sticky="ew")
        ttk.Label(heading, text="Timeline", style="Heading.TLabel").pack(side="left")
        ttk.Label(heading, textvariable=self.timeline_label, style="Badge.TLabel", padding=(6, 3)).pack(side="right")
        ttk.Label(panel, text="Progres total", style="Field.TLabel").grid(sticky="w", pady=(6, 0))
        ttk.Label(panel, textvariable=self.stage_count, style="Muted.TLabel").grid(row=1, column=0, sticky="e", pady=(6, 0))
        self.progress = StageBar(panel, height=11)
        self.progress.grid(sticky="ew", pady=(3, 5))
        self.caption(panel, self.status, 278).grid(sticky="ew", pady=(0, 5))
        self.stage_list = ttk.Frame(panel, style="Surface.TFrame")
        self.stage_list.grid(sticky="nsew")
        panel.rowconfigure(self.stage_list.grid_info()["row"], weight=1)
        self.stage_list.columnconfigure(1, weight=1)
        self.stage_list.columnconfigure(3, minsize=88)
        self.stage_rows = []
        self.caption(panel, self.details, 278).grid(sticky="ew", pady=(5, 0))

    def build_results(self, panel):
        ttk.Label(panel, text="Pratinjau hasil", style="Heading.TLabel").pack(anchor="w")
        self.result_name = tk.StringVar(value="Belum ada hasil")
        self.result_nav = ttk.Frame(panel, style="Surface.TFrame")
        self.previous_result = ttk.Button(self.result_nav, text="‹", width=2, state="disabled", command=lambda: self.move_result(-1))
        self.next_result = ttk.Button(self.result_nav, text="›", width=2, state="disabled", command=lambda: self.move_result(1))
        self.previous_result.pack(side="left", padx=(0, 4))
        self.next_result.pack(side="left", padx=(0, 7))
        ttk.Label(self.result_nav, textvariable=self.result_name, style="Value.TLabel", width=1).pack(
            side="left", fill="x", expand=True,
        )
        self.result_nav.pack(fill="x", pady=(4, 1))
        self.caption(panel, self.preview_status, 255).pack(fill="x", pady=(2, 10))
        toolbar = ttk.Frame(panel, style="Surface.TFrame")
        toolbar.pack(fill="x", pady=(0, 6))
        self.zoom_out = ttk.Button(toolbar, text="−", width=2, command=lambda: self.change_zoom(-1), state="disabled")
        self.zoom_in = ttk.Button(toolbar, text="+", width=2, command=lambda: self.change_zoom(1), state="disabled")
        self.fit_button = ttk.Button(toolbar, text="Pas", command=self.fit_preview, state="disabled")
        self.zoom_text = tk.StringVar(value="100%")
        for button in (self.zoom_out, self.zoom_in, self.fit_button):
            button.pack(side="left", padx=(0, 4))
        ttk.Label(toolbar, textvariable=self.zoom_text, style="Muted.TLabel").pack(side="left", padx=3)
        self.tone_button = ttk.Button(toolbar, text="Auto Tone Gambar", command=self.toggle_result_tone, state="disabled")
        self.tone_button.pack(side="right", padx=(5, 0))
        self.canvas = tk.Canvas(panel, bg=VIEWER, width=200, height=300, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.resize_preview)
        self.before_pane = PreviewPane(self.canvas)
        self.after_pane = PreviewPane(self.canvas)
        for view in (self.canvas, self.before_pane, self.after_pane):
            view.bind("<Button-1>", self.start_pan)
            view.bind("<B1-Motion>", self.drag_pan)
            view.bind("<MouseWheel>", self.wheel_zoom)
        mask = rounded_image(self.root, 18, SURFACE, mask=True)
        self.preview_corners = corner_tiles(self.root, mask, 18)
        self.canvas_text = "Belum ada hasil"

        compare_slot = ttk.Frame(panel, style="Surface.TFrame")
        compare_slot.pack(fill="x", pady=(6, 8))
        compare_slot.columnconfigure(0, weight=1)
        compare_row = self.compare_row = ttk.Frame(compare_slot, style="Surface.TFrame")
        compare_row.grid(row=0, column=0, sticky="ew")
        self.tone_hint = ttk.Label(compare_slot, text="Hasil Auto Tone", style="Muted.TLabel")
        ttk.Label(compare_row, text="Sebelum", style="Muted.TLabel").pack(side="left")
        ttk.Label(compare_row, text="Sesudah", style="Muted.TLabel").pack(side="right")
        self.split = tk.DoubleVar(value=50)
        self.slider = ttk.Scale(
            compare_row, from_=0, to=100, orient="horizontal", variable=self.split,
            command=self.request_preview_draw, state="disabled", takefocus=True, style="Compare.Horizontal.TScale",
        )
        self.slider.pack(side="left", fill="x", expand=True, padx=8)
        compare_slot.rowconfigure(0, minsize=max(self.tone_hint.winfo_reqheight(), self.slider.winfo_reqheight()))

    def toggle_result_tone(self):
        if self.photos:
            self.enhanced_view = not self.enhanced_view
            self.update_comparison_controls()
            if not self.enhanced_view:
                self.queue_zoom()
            self.request_preview_draw()

    def update_comparison_controls(self):
        self.tone_button.configure(text="Bandingkan" if self.enhanced_view else "Auto Tone Gambar")
        self.tone_button.state(["!disabled" if self.photos else "disabled"])
        comparable = self.photos and self.photos[0] is not None and not self.enhanced_view
        self.slider.state(["!disabled" if comparable else "disabled"])
        if self.enhanced_view:
            self.compare_row.grid_remove()
            self.tone_hint.grid(row=0, column=0, sticky="w")
        else:
            self.tone_hint.grid_remove()
            self.compare_row.grid()

    @staticmethod
    def action(parent, title, command, row, column):
        button = ttk.Button(parent, text=title, command=command, state="disabled")
        button.grid(row=row, column=column, sticky="ew", padx=(0, 6) if column == 0 else (0, 0), pady=(0, 4))
        return button

    def build_iterations(self, panel):
        ttk.Label(panel, text="Iterasi proses", style="Heading.TLabel").pack(anchor="w")
        self.iteration_title = tk.StringVar()
        ttk.Label(panel, textvariable=self.iteration_title, style="Muted.TLabel", width=1).pack(
            fill="x", pady=(2, 10),
        )
        navigation = ttk.Frame(panel, style="Surface.TFrame")
        navigation.pack(fill="x", pady=(0, 7))
        self.prev_stage = ttk.Button(navigation, text="‹", width=2, command=lambda: self.select_iteration(-1))
        self.next_stage = ttk.Button(navigation, text="›", width=2, command=lambda: self.select_iteration(1))
        self.follow_stage = ttk.Button(navigation, text="Ikuti proses", command=self.follow_active_stage)
        for button in (self.prev_stage, self.next_stage, self.follow_stage):
            button.pack(side="left", padx=(0, 4))
        ttk.Separator(panel).pack(fill="x")
        self.iteration_text = tk.Text(
            panel, width=24, height=12, wrap="word", font=("Consolas", 9),
            bg=SURFACE, fg=INK, relief="flat", borderwidth=0, highlightthickness=0,
            padx=2, pady=5, state="disabled",
        )
        self.iteration_text.pack(fill="both", expand=True, pady=(4, 0))
        ttk.Separator(panel).pack(fill="x")
        actions = ttk.Frame(panel, style="Surface.TFrame")
        actions.pack(fill="x", pady=(8, 0))
        self.folder_result = self.action(actions, "Folder hasil", lambda: self.open_file(self.output), 0, 0)
        self.log_result = self.action(actions, "Buka log", self.open_log, 0, 1)
        self.original_result = self.action(actions, "Gambar asli", lambda: self.open_file(self.original), 1, 0)
        self.enhanced_result = self.action(actions, "PNG hasil", lambda: self.open_file(self.enhanced), 1, 1)
        for column in range(2):
            actions.columnconfigure(column, weight=1, uniform="actions")

    def clear_iterations(self):
        self.iterations = []
        self.iteration_index = 0
        self.active_iteration = 0
        self.follow_iterations = True
        self.engine_offset = 0
        self.engine_stage = -1
        self.engine_lines.clear()
        self.render_iterations()

    def select_iteration(self, direction):
        self.follow_iterations = False
        self.iteration_index += direction
        self.render_iterations()

    def follow_active_stage(self):
        self.follow_iterations = True
        self.iteration_index = self.active_iteration
        self.render_iterations()

    def refresh_iterations(self, lines, active_index):
        stage = -1
        records = []
        percent = None
        for line in lines:
            if line.startswith("MULAI  "):
                stage += 1
                records.append(deque(maxlen=70))
                percent = None
            if stage < 0:
                continue
            if line.startswith("PROGRES  "):
                percent = line.partition("  ")[2]
                continue
            if line.startswith(("SELESAI  ", "GAGAL  ")):
                percent = None
            if line.strip():
                records[stage].append(line)
        if percent is not None and stage >= 0:
            records[stage].append(f"Progres terakhir · {percent}%")

        try:
            with (self.output / "metashape.log").open("rb") as engine:
                engine.seek(self.engine_offset)
                for line in engine.readlines(256 * 1024):
                    if not line.endswith(b"\n"):
                        break
                    self.engine_offset += len(line)
                    entry = line.decode("utf-8", errors="replace").strip()
                    if entry.startswith("ORTO TAHAP  "):
                        self.engine_stage += 1
                        self.engine_lines[self.engine_stage] = deque(maxlen=100)
                    elif self.engine_stage >= 0 and entry:
                        self.engine_lines[self.engine_stage].append(entry)
        except OSError:
            pass

        self.iterations = records
        self.active_iteration = min(active_index, len(self.stages) - 1)
        if self.follow_iterations:
            self.iteration_index = self.active_iteration
        self.render_iterations()

    def render_iterations(self):
        index = self.iteration_index
        self.iteration_title.set(f"{index + self.first_stage}/{len(self.stages) + self.first_stage - 1} · {self.stages[index]}")
        for button, enabled in (
            (self.prev_stage, index > 0), (self.next_stage, index + 1 < len(self.stages)),
            (self.follow_stage, not self.follow_iterations),
        ):
            button.state(["!disabled" if enabled else "disabled"])
        if index < len(self.iterations):
            summary = "\n".join(self.iterations[index])
            engine = "\n".join(self.engine_lines.get(index, ()))
            content = f"{summary}\n\nKELUARAN MESIN\n{engine}" if engine else summary
        else:
            content = "Rincian muncul saat tahap ini berjalan."
        text = self.iteration_text
        if text.get("1.0", "end-1c") != content:
            position = text.yview()[0]
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", content)
            text.configure(state="disabled")
            text.yview_moveto(1.0 if self.follow_iterations else position)

    def reset_preview(self, message):
        self.preview_pending = None
        self.zoom_revision += 1
        for timer in ("zoom_timer", "resize_timer", "draw_timer"):
            self.cancel_preview_timer(timer)
        self.master_previews = None
        self.photos = []
        self.zoom_cache = None
        self.zoom_region = None
        self.before_pane.hide()
        self.after_pane.hide()
        self.canvas_text = message
        self.enhanced_view = False
        self.update_comparison_controls()
        self.canvas.configure(cursor="")
        self.split.set(50)
        self.zoom_percent = self.display_zoom = 100
        self.zoom_motion = None
        self.pan = (0, 0)
        self.pan_start = None
        self.update_zoom_controls()
        self.request_preview_draw()

    def cancel_preview_timer(self, name):
        timer = getattr(self, name)
        if timer is not None:
            self.root.after_cancel(timer)
            setattr(self, name, None)

    def preview_unmapped(self, event):
        if event.widget is self.root:
            self.zoom_revision += 1
            for timer in ("zoom_timer", "resize_timer", "draw_timer"):
                self.cancel_preview_timer(timer)

    def preview_mapped(self, event):
        if event.widget is self.root:
            self.resize_preview()

    def update_zoom_controls(self):
        self.zoom_text.set(f"{round(self.display_zoom)}%")
        enabled = self.master_previews is not None
        for button, available in (
            (self.zoom_in, self.zoom_percent < ZOOM_LEVELS[-1]),
            (self.zoom_out, self.zoom_percent > ZOOM_LEVELS[0]),
            (self.fit_button, True),
        ):
            button.state(["!disabled" if enabled and available else "disabled"])

    def change_zoom(self, delta):
        if delta > 0:
            index = bisect_right(ZOOM_LEVELS, self.zoom_percent) + delta - 1
        else:
            index = bisect_left(ZOOM_LEVELS, self.zoom_percent) + delta
        self.zoom_to(ZOOM_LEVELS[max(0, min(len(ZOOM_LEVELS) - 1, index))])

    def wheel_zoom(self, event):
        anchor = (
            event.x_root - self.canvas.winfo_rootx() - self.canvas.winfo_width() / 2,
            event.y_root - self.canvas.winfo_rooty() - self.canvas.winfo_height() / 2,
        )
        self.zoom_to(self.zoom_percent * 1.17 ** (event.delta / 120), anchor)

    def zoom_to(self, percent, anchor=(0, 0)):
        target = max(ZOOM_LEVELS[0], min(ZOOM_LEVELS[-1], percent))
        if target != self.zoom_percent and self.master_previews is not None:
            target_pan = self.zoom_motion[3] if self.zoom_motion else self.pan
            ratio = target / self.zoom_percent
            pan = tuple(round(point + (offset - point) * ratio) for point, offset in zip(anchor, target_pan))
            self.begin_zoom_motion(target, pan)

    def begin_zoom_motion(self, target, pan):
        self.zoom_motion = (time.perf_counter(), self.display_zoom, self.pan, pan)
        self.zoom_percent = target
        self.update_zoom_controls()
        self.cancel_preview_timer("zoom_timer")
        self.schedule_zoom_frame()

    def fit_preview(self):
        if self.master_previews is not None:
            self.begin_zoom_motion(100, (0, 0))

    def schedule_zoom_frame(self):
        if (self.zoom_timer is None and self.resize_timer is None and
                self.zoom_job is None and self.canvas.winfo_ismapped()):
            self.zoom_timer = self.root.after(1, self.start_zoom_render)

    def resize_preview(self, _event=None):
        self.cancel_preview_timer("resize_timer")
        self.cancel_preview_timer("draw_timer")
        self.cancel_preview_timer("zoom_timer")
        self.zoom_revision += 1
        if self.canvas.winfo_ismapped():
            self.resize_timer = self.root.after(180, self.finish_preview_resize)

    def finish_preview_resize(self):
        self.resize_timer = None
        self.draw_preview()
        if self.master_previews is not None:
            self.start_zoom_render()

    def request_preview_draw(self, _value=None):
        if self.draw_timer is None and self.resize_timer is None and self.canvas.winfo_ismapped():
            self.draw_timer = self.root.after(0 if self.zoom_motion else 16, self.draw_scheduled_preview)

    def draw_scheduled_preview(self):
        self.draw_timer = None
        self.draw_preview()

    def queue_zoom(self):
        self.cancel_preview_timer("zoom_timer")
        self.zoom_revision += 1
        if self.resize_timer is None and self.canvas.winfo_ismapped():
            self.zoom_timer = self.root.after(25, self.start_zoom_render)

    def start_zoom_render(self):
        self.zoom_timer = None
        if self.master_previews is None or self.zoom_job is not None or not self.canvas.winfo_ismapped():
            return
        source_size = self.master_previews[0].size
        viewport = (self.canvas.winfo_width(), self.canvas.winfo_height())
        motion = self.zoom_motion
        percent, pan, moving = self.zoom_percent, self.pan, False
        if motion is not None:
            started, initial, origin, destination = motion
            fraction = min(1, (time.perf_counter() - started) / ZOOM_DURATION)
            eased = fraction * (2 - fraction)
            percent = initial * (self.zoom_percent / initial) ** eased
            pan_fraction = (percent - initial) / (self.zoom_percent - initial) if self.zoom_percent != initial else eased
            pan = tuple(round(a + (b - a) * pan_fraction) for a, b in zip(origin, destination))
            moving = fraction < 1
        fit = min(max(1, view - 24) / source for view, source in zip(viewport, source_size))
        factor = fit * percent / 100
        full = tuple(max(1, round(source * factor)) for source in source_size)
        pan = self.limit_pan(pan, full, viewport)
        visible = visible_bounds(full, viewport, pan)
        if (motion is None and self.zoom_region is not None and self.zoom_region.covers(full, visible)
                and (self.enhanced_view or self.photos[0] is not None)):
            return
        if (motion is None and self.zoom_cache is not None and self.zoom_cache[0].covers(full, visible)
                and (self.enhanced_view or self.zoom_cache[1][0] is not None)):
            self.show_zoom_photos(*self.zoom_cache)
            return
        region = zoom_region(full, source_size, visible, margin=12 if moving else ZOOM_MARGIN)
        revision = self.zoom_revision
        self.zoom_job = queue.Queue()
        threading.Thread(
            target=render_zoom, args=(self.master_previews, self.zoom_job, region, moving, self.enhanced_view),
            daemon=True,
        ).start()
        frame = (region, motion, moving, percent, pan)
        self.root.after(4, self.refresh_zoom, self.zoom_job, revision, frame)

    def refresh_zoom(self, job, revision, frame):
        try:
            previews = job.get_nowait()
        except queue.Empty:
            self.root.after(4, self.refresh_zoom, job, revision, frame)
            return
        self.zoom_job = None
        if revision != self.zoom_revision or not self.canvas.winfo_ismapped():
            if self.resize_timer is None and self.zoom_timer is None:
                self.start_zoom_render()
            return
        if previews is not None:
            try:
                if not hasattr(self, "photo_writer"):
                    self.photo_writer = PhotoWriter(self.root)
                region, motion, moving, percent, pan = frame
                if motion is not None:
                    self.pan = pan
                    if self.zoom_motion is motion and not moving:
                        self.zoom_motion = None
                self.display_zoom = percent
                self.zoom_text.set(f"{round(percent)}%")
                photos = [self.photo_writer.photo(preview) if preview is not None else None for preview in previews]
                self.show_zoom_photos(region, photos)
                if self.zoom_motion is not None:
                    self.schedule_zoom_frame()
                else:
                    self.request_visible_region()
                return
            except (tk.TclError, RuntimeError, OSError, AttributeError):
                pass
        self.zoom_motion = None
        self.zoom_percent = self.display_zoom
        self.update_zoom_controls()
        self.preview_status.set("Pratinjau tidak dapat ditampilkan. Gunakan tombol untuk membuka gambar.")
        self.canvas_text = "Pratinjau tidak tersedia"
        self.request_preview_draw()

    def show_zoom_photos(self, region, photos):
        first = not self.photos
        comparison_changed = not first and (self.photos[0] is None) != (photos[0] is None)
        self.zoom_cache = (self.zoom_region, self.photos) if self.zoom_region is not None else None
        self.zoom_region = region
        self.photos = photos
        if first or (comparison_changed and not self.enhanced_view):
            self.update_comparison_controls()
        if first:
            self.canvas.configure(cursor="fleur")
            self.preview_status.set("Roda mouse: zoom · Seret: geser")
        self.request_preview_draw()

    def start_pan(self, event):
        if self.photos:
            if self.zoom_motion is not None:
                self.zoom_motion = None
                self.zoom_percent = self.display_zoom
                self.zoom_revision += 1
                self.cancel_preview_timer("zoom_timer")
                self.update_zoom_controls()
            self.pan_start = (event.x_root - self.pan[0], event.y_root - self.pan[1])

    def drag_pan(self, event):
        if self.zoom_region is not None and self.pan_start is not None:
            viewport = (self.canvas.winfo_width(), self.canvas.winfo_height())
            x, y = event.x_root - self.pan_start[0], event.y_root - self.pan_start[1]
            self.pan = self.limit_pan((x, y), self.zoom_region.full, viewport)
            self.request_preview_draw()
            self.request_visible_region()

    @staticmethod
    def limit_pan(pan, full, viewport):
        limits = [max(0, (view + size) // 2 - min(24, size)) for size, view in zip(full, viewport)]
        return tuple(max(-limit, min(limit, offset)) for offset, limit in zip(pan, limits))

    def request_visible_region(self):
        if self.zoom_job is None and self.zoom_timer is None and self.zoom_region is not None:
            viewport = (self.canvas.winfo_width(), self.canvas.winfo_height())
            full = self.zoom_region.full
            if not self.zoom_region.covers(full, visible_bounds(full, viewport, self.pan)):
                self.queue_zoom()

    def draw_preview(self, _event=None):
        canvas = self.canvas
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 36 or height < 36 or not canvas.winfo_ismapped():
            return
        if (width, height) != self.draw_size:
            self.draw_size = (width, height)
            canvas.delete("corner")
            edges = ((0, 0), (width, 0), (width, height), (0, height))
            for edge, corner, anchor in zip(edges, self.preview_corners, ("nw", "ne", "se", "sw")):
                canvas.create_image(*edge, image=corner, anchor=anchor, tags="corner")
        canvas.delete("content")
        if self.photos:
            self.draw_comparison(width, height)
        else:
            canvas.create_text(width / 2, height / 2, text=self.canvas_text, fill="#d5e5e4",
                               font=("Segoe UI", 12), tags="content")
        canvas.tag_raise("corner")

    def draw_comparison(self, width, height):
        before, after = self.photos
        split = 0 if self.enhanced_view or before is None else round(width * self.split.get() / 100)
        full_width, full_height = self.zoom_region.full
        tile_x, tile_y, _, _ = self.zoom_region.bounds
        left = (width - full_width) // 2 + self.pan[0] + tile_x
        top = (height - full_height) // 2 + self.pan[1] + tile_y
        self.before_pane.show(before, left, top, 0, split, width, height, self.preview_corners)
        self.after_pane.show(after, left, top, split, width, width, height, self.preview_corners, divider=split > 0)


    def pick_file(self):
        filters = [
            ("Video", tuple(f"*{ext}" for ext in sorted(VIDEO_TYPES))),
            ("Antrean campuran (video, TIFF, PNG)", tuple(f"*{ext}" for ext in sorted(INPUT_TYPES))),
        ]
        selected = filedialog.askopenfilenames(
            parent=self.root, title="Pilih video",
            filetypes=filters,
        )
        if selected:
            self.set_sources(selected)

    def set_source(self, selected):
        self.set_sources((selected,))

    def pick_frames(self):
        selected = filedialog.askopenfilenames(
            parent=self.root, title="Pilih minimal 3 foto frame untuk satu orthomosaic",
            filetypes=[("Foto frame (JPEG, PNG, TIFF)", tuple(f"*{ext}" for ext in sorted(FRAME_TYPES)))],
        )
        if selected:
            self.set_sources(selected, frame_group=True)

    def set_sources(self, selected, frame_group=False):
        paths = list(dict.fromkeys(Path(name).absolute() for name in selected))
        if not paths or self.batch_index is not None:
            return
        if frame_group and len(paths) < 3:
            messagebox.showerror("Foto frame", "Pilih sedikitnya 3 foto frame berbeda dari area yang sama.", parent=self.root)
            return
        extensions = FRAME_TYPES if frame_group else INPUT_TYPES
        try:
            for path in paths:
                if not stat.S_ISREG(path.stat().st_mode):
                    raise ValueError("Lokasi ini bukan file. Pilih video, TIFF, atau PNG.")
                if path.suffix.lower() not in extensions:
                    raise ValueError(f"Ekstensi {path.suffix or '(kosong)'} tidak didukung.")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Input tidak valid", f"{path.name}\n\n{exc}", parent=self.root)
            return
        self.batch = [QueueEntry(paths[0], frames=tuple(paths))] if frame_group else [QueueEntry(path) for path in paths]
        self.viewed_result = None
        self.update_result_navigation()
        if not self.keep_destination:
            self.destination.set(str(paths[0].parent))
        if any(not job.enhance_only for job in self.batch):
            self.accuracy_row.grid()
        else:
            self.accuracy_row.grid_remove()
        self.display_input(self.batch[0])
        self.run_button.state(["!disabled"])
        self.reset_button.state(["disabled"])
        self.queue_button.state(["!disabled"])
        self.update_queue()

    def pick_accuracy(self):
        self.accuracy_menu.tk_popup(self.accuracy_button.winfo_rootx(),
                                    self.accuracy_button.winfo_rooty() + self.accuracy_button.winfo_height())

    def reset_inputs(self):
        if self.batch_index is not None:
            return
        self.keep_destination = self.source is not None
        self.batch.clear()
        self.source = None
        self.output = None
        self.viewed_result = None
        self.stages = VIDEO_STAGES
        self.first_stage = 1
        self.input_text.set("Belum ada file yang dipilih.")
        self.mode.set("Pilih video, foto frame, atau gambar untuk Auto Tone.")
        self.timeline_label.set("Pratinjau alur video")
        self.status.set("Menunggu input.")
        self.badge.set("Menunggu input")
        self.badge_label.configure(style="Badge.TLabel")
        self.details.set("")
        self.accuracy_row.grid_remove()
        self.clear_iterations()
        self.show_stages([])
        self.reset_preview("Belum ada hasil")
        self.preview_status.set("Hasil akan muncul setelah proses selesai.")
        self.update_result_navigation()
        for button in (self.folder_result, self.log_result, self.original_result, self.enhanced_result,
                       self.run_button, self.queue_button, self.reset_button):
            button.state(["disabled"])
        self.update_queue()

    def display_input(self, job, reviewing=False, starting=False):
        self.source = job.source
        self.input_text.set(job.title)
        if job.frames:
            mode, flow, self.stages = "Foto frame — buat satu orthomosaic", "Alur foto frame", VIDEO_STAGES[1:]
        elif job.enhance_only:
            mode, flow, self.stages = "Gambar — Auto Tone adaptif", "Alur gambar", IMAGE_STAGES
        else:
            mode, flow, self.stages = "Video — buat orthomosaic dan jalankan Auto Tone", "Alur video", VIDEO_STAGES
        self.first_stage = 2 if job.frames else 1
        self.mode.set(mode)
        self.clear_iterations()
        self.timeline_label.set(flow)
        self.status.set("Menyiapkan proses..." if starting else "Siap diproses.")
        if not (reviewing or starting):
            self.badge.set("Siap diproses")
            self.badge_label.configure(style="Badge.TLabel")
        self.details.set("")
        self.output = None
        self.reset_preview("Sedang memproses" if starting else "Siap diproses")
        self.preview_status.set("Proses berjalan. Log dan folder hasil dapat dibuka." if starting else
                                "Hasil akan muncul setelah proses selesai.")
        for button in (self.folder_result, self.log_result, self.original_result, self.enhanced_result):
            button.state(["disabled"])
        self.show_stages([])

    def open_queue(self):
        if self.queue_window is not None:
            self.queue_window.lift()
            return
        window = self.queue_window = tk.Toplevel(self.root)
        window.title("Antrean pemrosesan")
        window.transient(self.root)
        window.minsize(430, 270)
        window.protocol("WM_DELETE_WINDOW", self.close_queue)
        panel = ttk.Frame(window, style="Surface.TFrame", padding=14)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Antrean pemrosesan", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(panel, text="File dijalankan satu per satu dari atas.", style="Muted.TLabel").pack(anchor="w")
        self.queue_list = tk.Listbox(panel, height=7, font=("Segoe UI", 10), bg=SURFACE, fg=INK,
                                    borderwidth=0, highlightthickness=0, exportselection=False)
        self.queue_list.pack(fill="both", expand=True, pady=8)
        self.queue_list.bind("<<ListboxSelect>>", self.show_queue_entry)
        self.queue_list.bind("<Double-Button-1>", self.view_queued_result)
        self.queue_list.bind("<Return>", self.view_queued_result)
        self.queue_detail = tk.StringVar()
        ttk.Label(panel, textvariable=self.queue_detail, style="Muted.TLabel", wraplength=400).pack(anchor="w")
        actions = ttk.Frame(panel, style="Surface.TFrame")
        actions.pack(fill="x", pady=(7, 0))
        self.queue_view = ttk.Button(actions, text="Tampilkan hasil", command=self.view_queued_result)
        self.queue_view.pack(side="left", padx=(0, 6))
        self.queue_open = ttk.Button(actions, text="Buka folder hasil", command=self.open_queued_output)
        self.queue_open.pack(side="left")
        self.update_queue()

    def close_queue(self):
        self.queue_window.destroy()
        self.queue_window = None

    def update_queue(self):
        self.queue_caption.set(f"Antrean ({len(self.batch)})")
        if self.queue_window is None:
            return
        selected = self.queue_list.curselection()
        position = self.queue_list.yview()[0]
        self.queue_list.delete(0, "end")
        for index, job in enumerate(self.batch, 1):
            self.queue_list.insert("end", f"{index}. {job.title}   ·   {job.status}")
        if self.batch:
            index = min(selected[0], len(self.batch) - 1) if selected else self.batch_index or 0
            self.queue_list.selection_set(index)
            self.queue_list.yview_moveto(position)
        self.show_queue_entry()

    def show_queue_entry(self, _event=None):
        selected = self.queue_list.curselection()
        job = self.batch[selected[0]] if selected else None
        self.queue_detail.set((job.error or str(job.output or job.source)) if job else "")
        self.queue_open.state(["!disabled" if job and job.output else "disabled"])
        can_view = job and job.status == "Selesai" and self.batch_index is None
        self.queue_view.state(["!disabled" if can_view else "disabled"])

    def view_queued_result(self, _event=None):
        selected = self.queue_list.curselection()
        if selected and self.show_result(selected[0]):
            self.close_queue()

    def open_queued_output(self):
        selected = self.queue_list.curselection()
        if selected and self.batch[selected[0]].output:
            self.open_file(self.batch[selected[0]].output)

    def pick_folder(self, title="Pilih folder hasil", initial=None):
        current = self.destination.get() if self.source else str(Path.home())
        selected = filedialog.askdirectory(
            parent=self.root, title=title, initialdir=initial or current, mustexist=True,
        )
        if selected:
            self.destination.set(selected)
            self.keep_destination = True
        return selected

    def run(self):
        if not self.batch or self.batch_index is not None:
            return
        try:
            find_ffmpeg()
        except RuntimeError as exc:
            messagebox.showerror("Proses tidak dapat dimulai", str(exc), parent=self.root)
            return
        self.batch = [QueueEntry(job.source, frames=job.frames) for job in self.batch]
        self.job_command = "run"
        self.viewed_result = None
        self.update_result_navigation()
        self.batch_index = 0
        for button in (self.pick_button, self.pick_frames_button, self.folder_button, self.run_button,
                       self.accuracy_button, self.reset_button):
            button.state(["disabled"])
        for button in (self.pause_button, self.stop_button):
            button.state(["!disabled"])
        self.start_batch_entry()

    def start_batch_entry(self):
        if self.batch_index is None or self.process is not None:
            return
        if self.job_command == "pause":
            self.show_control_status(paused=True)
            return
        if self.job_command == "stop":
            self.finish_batch(stopped=True)
            return
        job = self.batch[self.batch_index]
        self.display_input(job, starting=True)
        job.status = "Berjalan"
        self.update_queue()
        is_image = job.enhance_only
        try:
            if not is_image and not METASHAPE.is_file():
                raise RuntimeError(f"Metashape Pro tidak ditemukan di {METASHAPE}")
            kind = "autotone" if is_image else "ortho"
            name = (job.source.parent.name or job.source.stem) if job.frames else job.source.stem
            while True:
                try:
                    job.output = create_output(Path(self.destination.get()), name, kind)
                    break
                except PermissionError:
                    if not self.pick_folder("Folder hasil tidak dapat ditulis. Pilih folder lain.", str(Path.home())):
                        raise RuntimeError("Folder hasil tidak dipilih.") from None
            self.output = job.output
            arguments = (job.source, job.output, self.accuracy.get())
            self.process = start_job(*arguments, job.frames) if job.frames else start_job(*arguments)
        except (RuntimeError, OSError) as exc:
            self.status.set(str(exc))
            self.badge.set("Proses gagal")
            self.badge_label.configure(style="ErrorBadge.TLabel")
            self.reset_preview("Proses tidak dapat dimulai")
            self.preview_status.set(str(exc))
            if job.output:
                self.folder_result.state(["!disabled"])
                self.log_result.state(["!disabled" if (job.output / "metashape.log").is_file() else "disabled"])
            self.finish_batch_entry(False, str(exc))
            return

        self.original = job.source if is_image else job.output / "orthomosaic_asli.tif"
        self.enhanced = job.output / "orthomosaic_enhanced.png"
        self.show_stages([])
        self.folder_result.state(["!disabled"])
        self.log_result.state(["!disabled"])
        self.update_queue()
        self.root.after(40, self.refresh)

    def toggle_pause(self):
        command = "run" if self.job_command == "pause" else "pause"
        self.set_job_command(command)

    def request_stop(self):
        if self.batch_index is not None and messagebox.askyesno(
            "Hentikan pemrosesan?",
            "Tahap aktif diselesaikan dulu. Sisa antrean akan dibatalkan. Hasil yang sudah selesai tetap tersimpan.",
            parent=self.root,
        ):
            self.set_job_command("stop")

    def set_job_command(self, command):
        if self.batch_index is None or self.job_command == "stop":
            return
        if self.process is not None:
            control = self.output / CONTROL_FILE
            temporary = control.with_suffix(".tmp")
            try:
                temporary.write_text(command, encoding="ascii")
                temporary.replace(control)
            except OSError as exc:
                messagebox.showerror("Kontrol proses", str(exc), parent=self.root)
                return
        self.job_command = command
        self.pause_button.configure(text="▶ Lanjut" if command == "pause" else "⏸ Jeda")
        if command == "stop":
            self.pause_button.state(["disabled"])
            self.stop_button.state(["disabled"])
        self.show_control_status(paused=self.process is None)
        if self.process is None:
            self.root.after_idle(self.start_batch_entry)

    def show_control_status(self, paused=False):
        if self.job_command == "pause":
            caption = "Dijeda" if paused else "Menunggu jeda"
            detail = "Klik Lanjut untuk meneruskan." if paused else "Jeda diterapkan setelah tahap aktif selesai."
        elif self.job_command == "stop":
            caption, detail = "Menunggu berhenti", "Tahap aktif sedang diselesaikan."
        else:
            return
        if self.badge.get() != caption:
            self.badge.set(caption)
            self.status.set(detail)
            self.badge_label.configure(style="WarningBadge.TLabel")

    def finish_batch_entry(self, success, error="", stopped=False):
        if self.batch_index is None:
            return False
        job = self.batch[self.batch_index]
        job.status = "Dihentikan" if stopped else "Selesai" if success else "Gagal"
        job.error = error
        self.update_queue()
        if self.job_command == "stop" or stopped:
            self.finish_batch(stopped=True)
        elif self.batch_index + 1 < len(self.batch):
            self.batch_index += 1
            self.root.after_idle(self.start_batch_entry)
        else:
            self.finish_batch()

    def finish_batch(self, stopped=False):
        if stopped:
            for job in self.batch:
                if job.status == "Menunggu":
                    job.status = "Dibatalkan"
        self.batch_index = None
        self.job_command = "run"
        self.pause_button.configure(text="⏸ Jeda")
        for button in (self.pause_button, self.stop_button):
            button.state(["disabled"])
        for button in (self.pick_button, self.pick_frames_button, self.folder_button, self.run_button,
                       self.accuracy_button, self.reset_button):
            button.state(["!disabled"])
        completed = sum(entry.status == "Selesai" for entry in self.batch)
        if stopped:
            self.badge.set(f"Dihentikan · {completed}/{len(self.batch)} selesai")
            self.badge_label.configure(style="WarningBadge.TLabel")
        elif len(self.batch) > 1:
            self.badge.set(f"Antrean selesai · {completed}/{len(self.batch)} berhasil")
            style = "DoneBadge" if completed == len(self.batch) else "WarningBadge" if completed else "ErrorBadge"
            self.badge_label.configure(style=f"{style}.TLabel")
        results = self.result_indices()
        if results:
            self.show_result(results[-1])
        self.update_queue()

    def result_indices(self):
        return [index for index, job in enumerate(self.batch) if job.status == "Selesai" and job.output]

    def update_result_navigation(self):
        if self.viewed_result is None:
            self.result_name.set("Belum ada hasil")
            self.previous_result.state(["disabled"])
            self.next_result.state(["disabled"])
            return
        results = self.result_indices()
        position = results.index(self.viewed_result)
        name = self.batch[self.viewed_result].title
        self.result_name.set(f"{position + 1}/{len(results)} · {name}")
        self.previous_result.state(["!disabled" if position > 0 else "disabled"])
        self.next_result.state(["!disabled" if position + 1 < len(results) else "disabled"])

    def move_result(self, direction):
        if self.viewed_result is not None:
            results = self.result_indices()
            position = results.index(self.viewed_result) + direction
            if 0 <= position < len(results):
                self.show_result(results[position])

    def show_result(self, index):
        if self.batch_index is not None or index not in self.result_indices():
            return False
        if index == self.viewed_result:
            return True
        job = self.batch[index]
        self.display_input(job, reviewing=True)
        self.viewed_result = index
        self.output = job.output
        self.original = job.source if job.enhance_only else job.output / "orthomosaic_asli.tif"
        self.enhanced = job.output / "orthomosaic_enhanced.png"
        if job.enhance_only:
            self.split.set(0)
        durations, _, _, _ = self.load_job_progress()
        self.show_stages(durations)
        self.status.set("Selesai. PNG enhanced tersimpan.")
        for button, available in (
            (self.folder_result, self.output.is_dir()),
            (self.log_result, (self.output / "proses.log").is_file() or (self.output / "metashape.log").is_file()),
            (self.original_result, self.original.is_file()),
            (self.enhanced_result, self.enhanced.is_file()),
        ):
            button.state(["!disabled" if available else "disabled"])
        self.update_result_navigation()
        if self.original.is_file() and self.enhanced.is_file():
            self.preview_status.set("Menyiapkan pratinjau...")
            self.canvas_text = "Menyiapkan pratinjau"
            self.preview_pending = (self.original, self.enhanced)
            self.start_result_preview()
        else:
            self.preview_status.set("File pratinjau tidak tersedia di lokasi semula.")
            self.canvas_text = "Pratinjau tidak tersedia"
        self.request_preview_draw()
        return True

    def show_stages(self, durations, active=False, failed=False, percent=None):
        completed = min(len(durations), len(self.stages))
        total = completed + (percent or 0) / 100 if completed < len(self.stages) else completed
        total_percent = 100 * total / len(self.stages)
        self.progress.update_progress(total_percent, ACCENT)
        count_text = f"{completed}/{len(self.stages)} tahap · {int(total_percent)}%"
        if self.stage_count.get() != count_text:
            self.stage_count.set(count_text)
        if self.process is not None and self.batch_index is not None and self.job_command == "run":
            prefix = f"File {self.batch_index + 1}/{len(self.batch)} · " if len(self.batch) > 1 else ""
            caption = f"{prefix}Memproses {int(total_percent)}%"
            if caption != self.badge.get():
                self.badge.set(caption)
            style = RUN_BADGES[min(int(total_percent // 34), len(RUN_BADGES) - 1)]
            if str(self.badge_label.cget("style")) != style:
                self.badge_label.configure(style=style)
        if len(self.stage_rows) != len(self.stages):
            for index in range(len(self.stage_rows)):
                self.stage_list.rowconfigure(index, weight=0)
            for widget in self.stage_list.winfo_children():
                widget.destroy()
            self.stage_rows.clear()
            spread = len(self.stages) > 1
            self.timeline_track = tk.Canvas(self.stage_list, width=14, height=18, bg=SURFACE, highlightthickness=0)
            self.timeline_track.grid(row=0, column=0, rowspan=len(self.stages), sticky="ns", padx=(0, 4))
            if spread:
                self.timeline_track.create_line(7, 0, 7, 18, fill=TRACK, width=2, tags="connector")
            for index, title in enumerate(self.stages):
                self.stage_list.rowconfigure(index, weight=int(spread))
                node = self.timeline_track.create_oval(3, 5, 11, 13, fill=TRACK, outline="")
                ttk.Label(self.stage_list, text=f"{index + self.first_stage}. {title}", style="Muted.TLabel").grid(
                    row=index, column=1, sticky="w",
                )
                bar = StageBar(self.stage_list)
                bar.grid(row=index, column=2, padx=(6, 0))
                state = ttk.Label(self.stage_list, width=1, anchor="e")
                state.grid(row=index, column=3, sticky="ew", padx=(5, 0))
                self.stage_rows.append((state, bar, node))
            self.timeline_track.bind("<Configure>", self.position_timeline_nodes)
        for index, (label, bar, node) in enumerate(self.stage_rows):
            state, style, value, color = "0%", "Muted", 0, TRACK
            if index < completed:
                duration = durations[index]
                state = f"✓ {duration} dtk" if duration else "✓ Selesai"
                style, value, color = "Active", 100, ACCENT
            if index == completed and (active or failed):
                value = percent or 0
                if failed:
                    state, style, color = "Gagal", "Error", "#a32629"
                else:
                    state = "Berjalan" if percent is None else f"{percent}%"
                    style, color = "Active", ACCENT
            if index == completed and active and percent is None:
                value = None
            bar.update_progress(value, color)
            if label.cget("text") != state or str(label.cget("style")) != f"{style}.TLabel":
                label.configure(text=state, style=f"{style}.TLabel")
            if self.timeline_track.itemcget(node, "fill") != color:
                self.timeline_track.itemconfigure(node, fill=color)

    def position_timeline_nodes(self, event):
        centers = []
        for index, (_, _, node) in enumerate(self.stage_rows):
            _, y, _, height = self.stage_list.grid_bbox(1, index)
            middle = y + height // 2
            centers.append(middle)
            event.widget.coords(node, 3, middle - 4, 11, middle + 4)
        if len(centers) > 1:
            event.widget.coords("connector", 7, centers[0], 7, centers[-1])

    def load_job_progress(self):
        try:
            with (self.output / "proses.log").open(encoding="utf-8", errors="replace") as log:
                lines = log.read().splitlines()
        except OSError:
            lines = []
        durations, percent, marker = timeline_status(lines)
        completed = len(durations)
        stage_index = completed if marker.startswith(("MULAI  ", "GAGAL  ")) else max(0, completed - 1)
        self.refresh_iterations(lines, stage_index)
        details = [line for line in lines if line.startswith(("Frame: ", "Kamera terselaras: ", "Total: "))]
        detail_text = "  ·  ".join(details)
        if self.details.get() != detail_text:
            self.details.set(detail_text)
        control = lines[-1] if lines and lines[-1] in (PAUSE_MARKER, STOP_MARKER) else ""
        return durations, percent, marker, control

    def refresh(self):
        exit_code = self.process.poll()
        durations, percent, marker, control = self.load_job_progress()
        completed = len(durations)
        if exit_code is None:
            active = marker.startswith("MULAI  ") and completed < len(self.stages)
            if active and self.job_command == "run":
                self.status.set(f"Tahap aktif · {self.stages[completed]}")
            self.show_stages(durations, active=active, percent=percent)
            self.show_control_status(paused=control == PAUSE_MARKER)
            self.root.after(750, self.refresh)
            return

        self.process = None
        if control == STOP_MARKER:
            self.show_stages(durations)
            self.status.set("Proses dihentikan di batas tahap.")
            self.reset_preview("Proses dihentikan")
            self.preview_status.set("Folder hasil memuat log dan file yang sempat disimpan.")
            self.finish_batch_entry(False, "Dihentikan setelah tahap aktif.", stopped=True)
            return
        try:
            success = exit_code == 0 and self.enhanced.stat().st_size > 0
        except OSError:
            success = False
        if success:
            self.show_stages(durations)
            self.status.set("Selesai. PNG enhanced tersimpan.")
            self.badge.set("Proses selesai")
            self.badge_label.configure(style="DoneBadge.TLabel")
            self.finish_batch_entry(True)
        else:
            failed = marker.startswith(("MULAI  ", "GAGAL  "))
            self.show_stages(durations, failed=failed, percent=percent)
            reason = f"Kode keluar {exit_code}." if exit_code else "PNG hasil tidak tersedia atau kosong."
            self.status.set(f"{reason} Buka log untuk rincian.")
            self.badge.set("Proses gagal")
            self.badge_label.configure(style="ErrorBadge.TLabel")
            self.canvas_text = "Proses gagal"
            self.draw_preview()
            self.preview_status.set("Proses gagal. Buka log untuk melihat rincian.")
            self.finish_batch_entry(False, f"{reason} Buka log di folder hasil.")

    def start_result_preview(self):
        if self.preview_job is not None or self.preview_pending is None:
            return
        paths = self.preview_pending
        self.preview_job = queue.Queue()
        threading.Thread(target=render_previews, args=(paths, self.preview_job), daemon=True).start()
        self.root.after(100, self.refresh_preview, self.preview_job, paths)

    def refresh_preview(self, job, paths):
        try:
            previews = job.get_nowait()
        except queue.Empty:
            self.root.after(100, self.refresh_preview, job, paths)
            return
        self.preview_job = None
        if paths != self.preview_pending:
            self.start_result_preview()
            return
        self.preview_pending = None
        if previews is not None:
            self.master_previews = previews
            self.update_zoom_controls()
            self.queue_zoom()
            return
        self.reset_preview("Pratinjau tidak tersedia")
        self.preview_status.set("Gunakan tombol di bawah untuk membuka gambar.")

    def open_log(self):
        log = self.output / "proses.log"
        self.open_file(log if log.is_file() and log.stat().st_size else self.output / "metashape.log")

    def open_file(self, path):
        try:
            os.startfile(path)
        except OSError as exc:
            messagebox.showerror("Tidak dapat membuka file", str(exc), parent=self.root)

    def close(self):
        if self.batch_index is not None:
            messagebox.showinfo("Proses berjalan", "Tunggu sampai proses selesai sebelum menutup jendela.", parent=self.root)
            return
        self.root.destroy()


def main():
    ctypes.windll.user32.SetProcessDPIAware()
    root = tk.Tk()
    root.withdraw()
    app = App(root)
    if len(sys.argv) > 1 and sys.argv[1]:
        app.set_source(sys.argv[1])
    root.update_idletasks()
    app.adjust_minimum()
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()
