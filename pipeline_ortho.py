import os
import math
import argparse
import json
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

ACCURACY_DOWNSCALE = {"Low": 4, "Medium": 2, "High": 1, "Highest": 0}
CONTROL_FILE = "kontrol-proses.txt"
STOP_MARKER = "DIHENTIKAN"
PAUSE_MARKER = "DIJEDA"


class JobStopped(Exception):
    pass


def log_line(log, message):
    print(message, file=log, flush=True)


def stage_checkpoint(log):
    control = Path(log.name).with_name(CONTROL_FILE)
    paused = False
    while True:
        try:
            command = control.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            command = "run"
        if command == "stop":
            log_line(log, STOP_MARKER)
            raise JobStopped
        if command != "pause":
            if paused:
                log_line(log, "DILANJUTKAN")
            return
        if not paused:
            log_line(log, PAUSE_MARKER)
            paused = True
        time.sleep(0.2)


@contextmanager
def stage(log, name):
    stage_checkpoint(log)
    log_line(log, f"MULAI  {name}")
    print(f"ORTO TAHAP  {name}", flush=True)
    started = time.perf_counter()
    last = -1

    def report(value):
        nonlocal last
        if math.isfinite(value):
            percent = min(99, max(0, int(value)))
            if percent > last:
                log_line(log, f"PROGRES  {percent}")
                last = percent

    try:
        yield report
    except Exception:
        log_line(log, f"GAGAL  {name}")
        raise
    log_line(log, f"SELESAI  {name} ({time.perf_counter() - started:.2f} detik)")


def run_ffmpeg(executable, arguments, log, report=None, duration=None):
    command = [str(executable), "-hide_banner", "-loglevel", "error", "-nostdin", "-n"]
    if duration is None:
        subprocess.run([*command, *arguments], stdout=log, stderr=subprocess.STDOUT, check=True)
        return

    command.extend(["-progress", "pipe:1", *arguments])
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log) as process:
        for line in process.stdout:
            key, _, value = line.strip().partition(b"=")
            if key == b"out_time_us" and value.isdigit():
                report(int(value) / duration / 10_000)
        if process.wait():
            raise subprocess.CalledProcessError(process.returncode, command)


def video_duration(ffmpeg, video):
    probe = ffmpeg.with_name("ffprobe.exe")
    if not probe.is_file():
        return None
    try:
        result = subprocess.run(
            [str(probe), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, check=True, timeout=10,
        )
        seconds = float(result.stdout)
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    return None


def find_ffmpeg():
    local = Path(__file__).resolve().parent / "ffmpeg" / "bin" / "ffmpeg.exe"
    if local.is_file():
        return local
    installed = shutil.which("ffmpeg.exe")
    if installed:
        return Path(installed).resolve()
    standard = Path(r"C:\ffmpeg\bin\ffmpeg.exe")
    if standard.is_file():
        return standard
    raise RuntimeError(
        "FFmpeg tidak ditemukan. Tambahkan folder bin FFmpeg ke PATH, "
        "atau tempatkan folder FFmpeg di sebelah launcher."
    )


def percentile_value(histogram, rank):
    total = 0
    for value in range(256):
        total += histogram[value]
        if total > rank:
            return value
    return 255


def adaptive_levels(ffmpeg, original, log):
    command = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(original), "-vf",
        "format=rgba,scale=w='min(iw,1280)':h='min(ih,4096)':"
        "force_original_aspect_ratio=decrease:flags=neighbor",
        "-frames:v", "1", "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1",
    ]
    pixels = subprocess.run(command, stdout=subprocess.PIPE, stderr=log, check=True).stdout
    alpha = pixels[3::4]
    opaque = alpha.count(255)
    alpha_threshold = 255 if opaque else 1
    count = opaque or len(alpha) - alpha.count(0)
    if not count:
        raise RuntimeError("Gambar tidak berisi piksel terlihat.")

    # Ignore the darkest and brightest 0.1% of each sampled RGB channel.
    clip = count // 1000
    levels = []
    for offset, channel in enumerate("rgb"):
        histogram = Counter(value for value, a in zip(pixels[offset::4], alpha) if a >= alpha_threshold)
        low = percentile_value(histogram, clip)
        high = percentile_value(histogram, count - clip - 1)
        log_line(log, f"{channel.upper()} titik hitam/putih: {low}/{high}")
        # Leave nearly flat channels unchanged to avoid amplifying noise.
        expression = f"clip((val-{low})*255/{high - low},0,255)" if high - low >= 16 else "val"
        levels.append(f"{channel}='{expression}'")
    return "format=rgba,lutrgb=" + ":".join(levels)


def enhance_orthomosaic(ffmpeg, original, output, log):
    with stage(log, "Auto Tone adaptif (histogram RGB)"):
        color_filter = adaptive_levels(ffmpeg, original, log)
        temporary = output / "enhanced_sementara.png"
        run_ffmpeg(ffmpeg, [
            "-i", str(original), "-vf", color_filter, "-frames:v", "1",
            "-pix_fmt", "rgba", "-compression_level", "1", "-update", "1", str(temporary),
        ], log)
        os.replace(temporary, output / "orthomosaic_enhanced.png")


def process_video(video, output, log, accuracy="Medium"):
    ffmpeg = find_ffmpeg()

    log_line(log, f"Video: {video}")
    started = time.perf_counter()
    frames = output / "frames"
    frames.mkdir()

    with stage(log, "Ekstraksi PNG (satu per 10 frame)") as report:
        run_ffmpeg(ffmpeg, [
            "-i", str(video), "-map", "0:v:0",
            "-vf", r"select='not(mod(n\,10))'", "-fps_mode", "passthrough",
            "-compression_level", "1", str(frames / "frame_%04d.png"),
        ], log, report, video_duration(ffmpeg, video))
        photos = sorted(frames.glob("frame_*.png"))
        if len(photos) < 3:
            raise RuntimeError(f"Hanya {len(photos)} frame terpilih; diperlukan minimal 3.")
        log_line(log, f"Frame: {len(photos)}")

    process_photos(photos, output, log, accuracy, ffmpeg)
    log_line(log, f"Total: {time.perf_counter() - started:.2f} detik")


def process_photos(photos, output, log, accuracy, ffmpeg, copy_frames=False):
    import Metashape

    log_line(log, f"Metashape {Metashape.app.version} | Akurasi pencocokan: {accuracy}")
    with stage(log, "Buat proyek Metashape"):
        if copy_frames:
            frames = output / "frames"
            frames.mkdir()
            copies = []
            for index, photo in enumerate(photos, 1):
                target = frames / f"frame_{index:04d}{photo.suffix.lower()}"
                shutil.copyfile(photo, target)
                copies.append(target)
            photos = copies
            log_line(log, f"Frame: {len(photos)}")
        document = Metashape.Document()
        document.save(str(output / "proyek.psx"))
        chunk = document.addChunk()
        chunk.addPhotos([str(photo) for photo in photos])
        document.save()

    with stage(log, f"Match Photos ({accuracy})") as report:
        chunk.matchPhotos(downscale=ACCURACY_DOWNSCALE[accuracy], generic_preselection=True, reference_preselection=False,
                          keypoint_limit=70000, tiepoint_limit=8000, progress=report)

    with stage(log, "Align Cameras") as report:
        chunk.alignCameras(adaptive_fitting=True, progress=report)
        aligned = sum(camera.transform is not None for camera in chunk.cameras)
        log_line(log, f"Kamera terselaras: {aligned}/{len(chunk.cameras)}")
        document.save()
        if aligned < 3:
            raise RuntimeError("Kurang dari 3 kamera terselaras. Periksa overlap dan ketajaman video.")

    with stage(log, "Build Model (Tie points, Arbitrary 3D)") as report:
        chunk.buildModel(source_data=Metashape.TiePointsData, surface_type=Metashape.Arbitrary,
                         face_count=Metashape.HighFaceCount, progress=report)
        if chunk.model is None:
            raise RuntimeError("Metashape tidak menghasilkan model permukaan.")
        document.save()

    with stage(log, "Build Orthomosaic (Planar, Top XY, Model, Mosaic)") as report:
        projection = Metashape.OrthoProjection()
        projection.type = Metashape.OrthoProjection.Type.Planar
        projection.crs = chunk.crs
        chunk.buildOrthomosaic(surface_data=Metashape.ModelData, projection=projection,
                               blending_mode=Metashape.MosaicBlending, fill_holes=True, progress=report)
        if chunk.orthomosaic is None:
            raise RuntimeError("Metashape tidak menghasilkan orthomosaic.")
        document.save()

    original = output / "orthomosaic_asli.tif"
    with stage(log, "Ekspor TIFF asli") as report:
        compression = Metashape.ImageCompression()
        compression.tiff_compression = Metashape.ImageCompression.TiffCompressionLZW
        chunk.exportRaster(path=str(original), source_data=Metashape.OrthomosaicData,
                           projection=projection, save_alpha=True, image_compression=compression,
                           progress=report)
        if not original.is_file() or original.stat().st_size == 0:
            raise RuntimeError("TIFF hasil ekspor tidak ditemukan atau kosong.")

    enhance_orthomosaic(ffmpeg, original, output, log)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--enhance", action="store_true")
    mode.add_argument("--frames", action="store_true")
    parser.add_argument("--accuracy", choices=ACCURACY_DOWNSCALE, default="Medium")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    input_file, output_folder = args.input.resolve(), args.output.resolve()
    with (output_folder / "proses.log").open("w", encoding="utf-8") as log:
        try:
            if args.enhance:
                log_line(log, f"Sumber gambar: {input_file}")
                enhance_orthomosaic(find_ffmpeg(), input_file, output_folder, log)
            elif args.frames:
                started = time.perf_counter()
                photos = [Path(name).resolve() for name in json.loads(input_file.read_text(encoding="utf-8"))]
                if len(set(photos)) < 3:
                    raise ValueError("Pilih sedikitnya 3 foto frame yang berbeda.")
                process_photos(photos, output_folder, log, args.accuracy, find_ffmpeg(), copy_frames=True)
                log_line(log, f"Total: {time.perf_counter() - started:.2f} detik")
            else:
                process_video(input_file, output_folder, log, args.accuracy)
        except JobStopped:
            pass
        except Exception:
            traceback.print_exc(file=log)
            raise
