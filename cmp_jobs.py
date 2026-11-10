import json
import math
import os
import struct
import subprocess
from dataclasses import dataclass
from itertools import count
from pathlib import Path
import sys
import tempfile

from pipeline_ortho import find_ffmpeg

NO_WINDOW = subprocess.CREATE_NO_WINDOW

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


@dataclass
class QueueEntry:
    source: Path
    status: str = "Menunggu"
    output: Path = None
    error: str = ""
    frames: tuple = ()
    rotation: float = 0.0
    focus: tuple = ()

    @property
    def enhance_only(self):
        return not self.frames and self.source.suffix.lower() in IMAGE_TYPES

    @property
    def title(self):
        return f"{len(self.frames)} foto frame · {self.source.parent.name}" if self.frames else self.source.name


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


@dataclass
class Raster:
    width: int
    height: int
    pixels: bytearray

    @property
    def size(self):
        return self.width, self.height

    @classmethod
    def from_bmp(cls, data):
        offset = struct.unpack_from("<I", data, 10)[0]
        width, signed_height, planes, bits, compression = struct.unpack_from("<iiHHI", data, 18)
        height = abs(signed_height)
        stride = width * 4
        if (data[:2] != b"BM" or width <= 0 or not height or
                (planes, bits, compression) != (1, 32, 0) or len(data) < offset + stride * height):
            raise ValueError("Format pratinjau BGRA tidak valid.")
        if signed_height < 0:
            pixels = bytearray(data[offset:offset + stride * height])
        else:
            rows = memoryview(data)[offset:offset + stride * height]
            pixels = bytearray().join(rows[y:y + stride] for y in range((height - 1) * stride, -1, -stride))
        return cls(width, height, pixels)


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


def export_rotated_png(source, target, angle, results):
    temporary = None
    try:
        ffmpeg = find_ffmpeg()
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".png", delete=False) as handle:
            temporary = Path(handle.name)
        radians = f"{math.radians(angle):.16g}"
        rotation = (
            f"format=rgba,rotate={radians}:ow=ceil(rotw({radians})-1e-9):"
            f"oh=ceil(roth({radians})-1e-9):c=black@0"
        )
        subprocess.run(
            [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
             "-vf", rotation, "-frames:v", "1", "-c:v", "png", "-pix_fmt", "rgba", "-update", "1", str(temporary)],
            capture_output=True, check=True, timeout=180, creationflags=NO_WINDOW,
        )
        temporary.replace(target)
        results.put(None)
    except subprocess.CalledProcessError as exc:
        results.put(exc.stderr.decode("utf-8", errors="replace").strip() or "FFmpeg gagal menyimpan gambar.")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        results.put(str(exc))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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





