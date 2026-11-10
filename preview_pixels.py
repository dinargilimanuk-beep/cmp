"""Render a viewport from decoded BGRA pixels and transfer it directly to Tk."""

import ctypes as c
from dataclasses import dataclass
from pathlib import Path
import struct
import sys
import tkinter as tk


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


class StartupInput(c.Structure):
    _fields_ = [("version", c.c_uint), ("debug", c.c_void_p),
                ("no_thread", c.c_int), ("no_codecs", c.c_int)]


def bind(library, name, *arguments):
    function = getattr(library, name)
    function.argtypes = arguments
    return function


def check(status):
    if status:
        raise RuntimeError(f"Gagal menggambar pratinjau (kode {status}).")


def scale_regions(sources, region, moving):
    graphics = c.windll.gdiplus
    pointer, integer = c.c_void_p, c.c_int
    startup = bind(graphics, "GdiplusStartup", c.POINTER(c.c_size_t), c.POINTER(StartupInput), pointer)
    shutdown = bind(graphics, "GdiplusShutdown", c.c_size_t)
    bitmap = bind(graphics, "GdipCreateBitmapFromScan0", integer, integer, integer, integer, pointer, c.POINTER(pointer))
    dispose = bind(graphics, "GdipDisposeImage", pointer)
    context = bind(graphics, "GdipGetImageGraphicsContext", pointer, c.POINTER(pointer))
    delete_context = bind(graphics, "GdipDeleteGraphics", pointer)
    interpolate = bind(graphics, "GdipSetInterpolationMode", pointer, integer)
    composite = bind(graphics, "GdipSetCompositingMode", pointer, integer)
    draw = bind(graphics, "GdipDrawImageRectRectI", pointer, pointer, *([integer] * 9), pointer, pointer, pointer)
    token = c.c_size_t()
    check(startup(c.byref(token), c.byref(StartupInput(1, None, 0, 1)), None))
    left, top, right, bottom = region.bounds
    width, height = right - left, bottom - top
    results = []
    try:
        for source in sources:
            x, y, end_x, end_y = (
                round(value * size / reference) for value, size, reference in
                zip(region.crop, source.size * 2, region.source_size * 2)
            )
            end_x, end_y = min(source.width, max(x + 1, end_x)), min(source.height, max(y + 1, end_y))
            x, y = min(x, end_x - 1), min(y, end_y - 1)
            source_bitmap, output_bitmap, painter = pointer(), pointer(), pointer()
            target = bytearray(width * height * 4)
            source_data = (c.c_ubyte * len(source.pixels)).from_buffer(source.pixels)
            target_data = (c.c_ubyte * len(target)).from_buffer(target)
            pixel_format_argb = 0x26200A
            try:
                check(bitmap(source.width, source.height, source.width * 4, pixel_format_argb,
                             source_data, c.byref(source_bitmap)))
                check(bitmap(width, height, width * 4, pixel_format_argb, target_data, c.byref(output_bitmap)))
                check(context(output_bitmap, c.byref(painter)))
                check(composite(painter, 1))  # SourceCopy keeps transparent pixels.
                check(interpolate(painter, 3 if moving else 7))  # Bilinear / high-quality bicubic.
                check(draw(painter, source_bitmap, 0, 0, width, height,
                           x, y, end_x - x, end_y - y, 2, None, None, None))
                results.append(Raster(width, height, target))
            finally:
                if painter:
                    delete_context(painter)
                if output_bitmap:
                    dispose(output_bitmap)
                if source_bitmap:
                    dispose(source_bitmap)
        return results
    finally:
        shutdown(token)


class PhotoBlock(c.Structure):
    _fields_ = [("pixels", c.c_void_p), ("width", c.c_int), ("height", c.c_int),
                ("pitch", c.c_int), ("pixel_size", c.c_int), ("offset", c.c_int * 4)]


class PhotoWriter:
    def __init__(self, root):
        major, minor = str(root.tk.call("package", "require", "Tk")).split(".")[:2]
        name = f"tk{major}{minor}t.dll"
        local = Path(sys.base_prefix) / "DLLs" / name
        api = c.CDLL(str(local) if local.is_file() else name)
        pointer, integer = c.c_void_p, c.c_int
        self.find = bind(api, "Tk_FindPhoto", pointer, c.c_char_p)
        self.find.restype = pointer
        self.put = bind(api, "Tk_PhotoPutBlock", pointer, pointer, c.POINTER(PhotoBlock), *([integer] * 5))
        self.root = root
        self.interpreter = root.tk.interpaddr()

    def photo(self, raster):
        photo = tk.PhotoImage(master=self.root, width=raster.width, height=raster.height)
        buffer = (c.c_ubyte * len(raster.pixels)).from_buffer(raster.pixels)
        block = PhotoBlock(c.addressof(buffer), raster.width, raster.height, raster.width * 4,
                           4, (c.c_int * 4)(2, 1, 0, 3))
        handle = self.find(self.interpreter, str(photo).encode("ascii"))
        if not handle or self.put(self.interpreter, handle, c.byref(block), 0, 0,
                                  raster.width, raster.height, 1):
            raise RuntimeError("Tk tidak dapat memuat piksel pratinjau.")
        return photo
