import math
import os
import queue
import sys
import threading
from pathlib import Path


BASE = Path(__file__).resolve().parent
METASHAPE = Path(sys.executable).resolve().parent.parent
qt_dlls = os.add_dll_directory(str(METASHAPE))
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(METASHAPE / "plugins" / "platforms")

from PySide2 import QtCore, QtGui, QtWidgets

from cmp_language import english
from cmp_jobs import (FRAME_TYPES, IMAGE_STAGES, INPUT_TYPES, VIDEO_STAGES, VIDEO_TYPES,
                      QueueEntry, create_output, export_rotated_png, render_previews, start_job, timeline_status)
from pipeline_ortho import ACCURACY_DOWNSCALE, CONTROL_FILE, PAUSE_MARKER, STOP_MARKER, find_ffmpeg

COMPANY_NAME = "PT Dinar Darum Lestari"
APP_TITLE = "Coral Marine Photogrammetry"
APP_DESCRIPTION = "Orthomosaic Visualisasi Bawah Air Budidaya Karang Hias"

STYLE = """
QMainWindow, QWidget#body { background: #f2f5f7; }
QScrollArea#sidebar, QWidget#sidebarContent { background: #f2f5f7; border: none; }
QScrollArea#sidebar QScrollBar:vertical, QScrollArea#guideScroll QScrollBar:vertical
    { background: #f2f5f7; width: 10px; margin: 0; }
QScrollArea#sidebar QScrollBar::handle:vertical, QScrollArea#guideScroll QScrollBar::handle:vertical
    { background: #a8c2bd; border-radius: 3px;
    min-height: 32px; margin: 0 2px; }
QScrollArea#sidebar QScrollBar::add-line:vertical, QScrollArea#sidebar QScrollBar::sub-line:vertical,
QScrollArea#guideScroll QScrollBar::add-line:vertical, QScrollArea#guideScroll QScrollBar::sub-line:vertical { height: 0; }
QScrollArea#sidebar QScrollBar::add-page:vertical, QScrollArea#sidebar QScrollBar::sub-page:vertical,
QScrollArea#guideScroll QScrollBar::add-page:vertical, QScrollArea#guideScroll QScrollBar::sub-page:vertical { background: none; }
QWidget { color: #14292e; font: 10pt "Segoe UI"; }
QFrame#panel, QWidget#header, QWidget#footer { background: white; }
QFrame#panel { border: 1px solid #bfd1cf; border-radius: 27px; }
QLabel#heading { font: 13pt "Segoe UI Semibold"; }
QLabel#title { font: 22pt "Segoe UI Semibold"; color: #02717a; }
QLabel#description { font: 10pt "Segoe UI Semibold"; color: #415c63; }
QLabel#section { font: 10pt "Segoe UI Semibold"; color: #294b50; }
QLabel#muted { color: #354f56; font-size: 9pt; }
QLabel#metric, QLabel#stageState { font: 9pt "Segoe UI Semibold"; color: #25464c; }
QLabel#stageState[active="true"] { color: #07554b; }
QLabel#value { color: #4d6369; background: #f2f6f6; border: 1px solid #d9e3e5;
    border-radius: 8px; padding: 2px 8px; font-size: 9pt; }
QLabel#badge, QLabel#flow { background: #e6f2ef; color: #086557;
    border-radius: 9px; padding: 4px 9px; font-size: 9pt; font-weight: 700; }
QLabel#badge[phase="running"] { background: #166a80; color: white; }
QLabel#badge[phase="working"] { background: #086557; color: white; }
QLabel#badge[phase="finishing"], QLabel#badge[phase="done"] { background: #087039; color: white; }
QLabel#badge[phase="warning"] { background: #8a510f; color: white; }
QLabel#badge[phase="error"] { background: #a32629; color: white; }
QDialog#guide, QScrollArea#guideScroll, QWidget#guideContent { background: white; border: none; }
QLabel#guideNumber { background: #e6f2ef; color: #086557; border-radius: 12px;
    font: 10pt "Segoe UI Semibold"; }
QPushButton { background: #eaf4f2; border: 1px solid #8eb4ad; border-bottom: 2px solid #789f99;
    border-radius: 9px; padding: 3px 11px 2px; }
QPushButton:hover { background: #dbeee9; border-color: #52988a; }
QPushButton:focus { background: #e1f2ee; border-color: #086557; }
QPushButton:pressed { background: #c9e3dd; border-color: #086557; }
QPushButton:disabled { background: #f4f6f6; border-color: #e1e7e8; color: #78868a; }
QPushButton#zoomButton { padding: 3px 5px 2px; }
QPushButton#primary { background: #086557; border-color: #086557; color: white; }
QPushButton#primary:hover, QPushButton#primary:pressed { background: #064e44; }
QPushButton#primary:focus { border-color: #102c43; }
QPushButton#primary:disabled { background: #f4f6f6; border-color: #e1e7e8; color: #78868a; }
QFrame#previewEmpty { background: #f1f7f6; border: 1px solid #dbe8e6; border-radius: 16px; }
QLabel#emptyHeading { font: 15pt "Segoe UI Semibold"; color: #234e51; }
QDialog#rotationDialog { background: white; }
QDoubleSpinBox { background: white; border: 1px solid #8eb4ad; border-radius: 8px; padding: 4px 8px; }
QComboBox { background: #eaf4f2; border: 1px solid #8eb4ad; border-radius: 9px; padding: 3px 24px 3px 10px; }
QComboBox:hover, QComboBox:focus { background: #e1f2ee; border-color: #086557; }
QComboBox:disabled { background: #f2f5f7; color: #64797f; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: none; }
QProgressBar { border: 0; background: #d5e5e2; border-radius: 4px; }
QProgressBar::chunk { background: #086557; border-radius: 4px; }
QSlider::groove:horizontal { height: 6px; background: #d5e5e2; border-radius: 3px; }
QSlider::handle:horizontal { width: 16px; margin: -5px 0;
    background: #086557; border-radius: 8px; }
QSlider::handle:horizontal:disabled { background: #9cb7b4; }
QFrame#divider { background: #bfd1cf; }
QPlainTextEdit { border: none; background: white; padding: 2px; font: 9pt Consolas; }
"""


def tr(text):
    app = QtWidgets.QApplication.instance()
    return english(text) if app is not None and app.property("cmpLanguage") == "en" else text


class LocalizedText:
    def setText(self, text):
        self.source_text = text
        super().setText(tr(text))

    def setToolTip(self, text):
        self.source_tooltip = text
        super().setToolTip(tr(text))

    def setAccessibleName(self, text):
        self.source_name = text
        super().setAccessibleName(tr(text))

    def retranslate(self):
        if hasattr(self, "source_text"):
            self.setText(self.source_text)
        if hasattr(self, "source_tooltip"):
            self.setToolTip(self.source_tooltip)
        if hasattr(self, "source_name"):
            self.setAccessibleName(self.source_name)


class LocalizedLabel(LocalizedText, QtWidgets.QLabel):
    def clear(self):
        self.setText("")


class LocalizedButton(LocalizedText, QtWidgets.QPushButton):
    pass


def label(text, style=""):
    widget = LocalizedLabel()
    widget.setText(text)
    widget.setObjectName(style)
    return widget


class ElidedLabel(QtWidgets.QLabel):
    def __init__(self, style="value", elide=QtCore.Qt.ElideMiddle):
        super().__init__()
        self.full_text = ""
        self.elide = elide
        self.horizontal_padding = 18 if style == "value" else 0
        self.setObjectName(style)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)

    def show_text(self, text):
        if text != self.full_text:
            self.full_text = text
            self.retranslate()

    def retranslate(self):
        self.setToolTip(tr(self.full_text))
        self.update_caption()

    def update_caption(self):
        available = max(0, self.width() - self.horizontal_padding)
        self.setText(self.fontMetrics().elidedText(tr(self.full_text), self.elide, available))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_caption()


def button(text, action, description=None):
    widget = LocalizedButton()
    widget.setText(text)
    widget.clicked.connect(action)
    if description:
        widget.setAccessibleName(description)
        widget.setToolTip(description)
    return widget


def action_icon(kind, color="#086557"):
    icon = QtGui.QIcon()
    for mode, fill in ((QtGui.QIcon.Normal, color), (QtGui.QIcon.Disabled, "#64797f")):
        image = QtGui.QPixmap(32, 32)
        image.setDevicePixelRatio(2)
        image.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(fill))
        if kind == "play":
            painter.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(5, 3), QtCore.QPointF(13, 8), QtCore.QPointF(5, 13)]))
        elif kind == "pause":
            for x in (4, 9):
                painter.drawRoundedRect(QtCore.QRectF(x, 3, 3, 10), 0.6, 0.6)
        elif kind == "stop":
            painter.drawRoundedRect(QtCore.QRectF(3.5, 3.5, 9, 9), 1, 1)
        elif kind == "reset":
            painter.setBrush(QtCore.Qt.NoBrush)
            pen = QtGui.QPen(QtGui.QColor(fill), 1.45)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawArc(QtCore.QRectF(3, 3, 10, 10), 48 * 16, 286 * 16)
            painter.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(9, 4.2), QtCore.QPointF(12.4, 5.2),
                                                QtCore.QPointF(12.4, 1.8)]))
        painter.end()
        icon.addPixmap(image, mode)
    return icon


def panel(title, trailing=None):
    frame = QtWidgets.QFrame()
    frame.setObjectName("panel")
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(6)
    if trailing is None:
        layout.addWidget(label(title, "heading"))
    else:
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(label(title, "heading"), 1)
        heading.addWidget(trailing)
        layout.addLayout(heading)
    return frame, layout


def divider():
    line = QtWidgets.QFrame()
    line.setObjectName("divider")
    line.setFixedHeight(1)
    return line


class TimelineRail(QtWidgets.QWidget):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows
        self.states = ()
        self.setFixedWidth(12)

    def show_states(self, states):
        if states != self.states:
            self.states = states
            self.update()

    def paintEvent(self, event):
        centers = [row[0].geometry().center().y() for row in self.rows[:len(self.states)]]
        if not centers:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor("#d5e5e2"), 2))
        x = self.width() // 2
        painter.drawLine(x, centers[0], x, centers[-1])
        painter.setPen(QtCore.Qt.NoPen)
        for y, active in zip(centers, self.states):
            painter.setBrush(QtGui.QColor("#086557" if active else "#d5e5e2"))
            painter.drawEllipse(QtCore.QPoint(x, y), 4, 4)


class AccuracyPicker(QtWidgets.QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor("#4d6369"), 1.3))
        x, y = self.width() - 14, self.height() / 2
        painter.drawPolyline(QtGui.QPolygonF([
            QtCore.QPointF(x - 4, y - 2), QtCore.QPointF(x, y + 2), QtCore.QPointF(x + 4, y - 2),
        ]))


def raster_pixmap(raster):
    pixels = bytes(raster.pixels)
    image = QtGui.QImage(pixels, raster.width, raster.height, raster.width * 4,
                         QtGui.QImage.Format_ARGB32)
    return QtGui.QPixmap.fromImage(image.copy())


def focus_path(points, rect):
    path = QtGui.QPainterPath()
    path.addPolygon(QtGui.QPolygonF([
        QtCore.QPointF(rect.left() + x * rect.width(), rect.top() + y * rect.height()) for x, y in points
    ]))
    path.closeSubpath()
    return path


def export_final_png(source, target, rotation, points, results):
    try:
        image = QtGui.QImage(str(source), "PNG")
        if image.isNull():
            raise RuntimeError("PNG Auto Tone tidak dapat dibuka.")
        transform = QtGui.QTransform().rotate(rotation)
        rect = QtCore.QRectF(image.rect())
        bounds = transform.mapRect(rect)
        width = math.ceil(bounds.width() - 1e-9)
        height = math.ceil(bounds.height() - 1e-9)
        result = QtGui.QImage(width, height, QtGui.QImage.Format_ARGB32_Premultiplied)
        if result.isNull():
            raise RuntimeError("Kanvas PNG hasil terlalu besar.")
        result.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(result)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
            painter.translate((width - bounds.width()) / 2 - bounds.left(),
                              (height - bounds.height()) / 2 - bounds.top())
            painter.rotate(rotation)
            if points:
                painter.setClipPath(focus_path(points, rect))
            painter.drawImage(0, 0, image)
        finally:
            painter.end()
        output = QtCore.QSaveFile(str(target))
        if not output.open(QtCore.QIODevice.WriteOnly):
            raise RuntimeError(output.errorString())
        writer = QtGui.QImageWriter(output, b"png")
        if not writer.write(result):
            output.cancelWriting()
            raise RuntimeError(writer.errorString())
        if not output.commit():
            raise RuntimeError(output.errorString())
        results.put(None)
    except (OSError, RuntimeError, MemoryError, TypeError) as exc:
        results.put(str(exc))


class ProgressBar(QtWidgets.QProgressBar):
    def __init__(self):
        super().__init__()
        self.setTextVisible(False)
        self.setValue(0)
        self.setFixedHeight(8)
        self.target = 0
        self.animation = QtCore.QVariantAnimation(self)
        self.animation.setDuration(145)
        self.animation.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self.setValue)

    def show_value(self, value):
        if self.target == value:
            return
        self.target = value
        self.animation.stop()
        self.setRange(0, 0 if value is None else 100)
        if value is None:
            return
        current = max(0, self.value())
        if value <= current:
            self.setValue(value)
            return
        self.animation.setStartValue(current)
        self.animation.setEndValue(value)
        self.animation.start()

    def paintEvent(self, event):
        if self.maximum() == 0:
            super().paintEvent(event)
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        bounds = QtCore.QRectF(self.rect())
        radius = bounds.height() / 2
        painter.setBrush(QtGui.QColor("#d5e5e2"))
        painter.drawRoundedRect(bounds, radius, radius)
        if self.value() > 0:
            bounds.setWidth(bounds.width() * self.value() / 100)
            painter.setBrush(QtGui.QColor("#086557"))
            painter.drawRoundedRect(bounds, radius, radius)


class PreviewMark(QtWidgets.QWidget):
    def __init__(self, logo):
        super().__init__()
        self.setFixedSize(160, 160)
        self.logo = logo.scaled(320, 320, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        self.busy = False
        self.clock = QtCore.QElapsedTimer()
        self.clock.start()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.update)

    def set_busy(self, busy):
        if self.busy != busy:
            self.busy = busy
            self.refresh_timer()
            self.update()

    def refresh_timer(self):
        if self.busy and self.isVisible() and not self.window().isMinimized():
            if not self.timer.isActive():
                self.timer.start()
        else:
            self.timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_timer()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        center = QtCore.QRectF(self.rect()).center()
        if not self.logo.isNull():
            scale = (94 if self.busy else 152) / max(self.logo.width(), self.logo.height())
            size = QtCore.QSizeF(self.logo.width() * scale, self.logo.height() * scale)
            target = QtCore.QRectF(center - QtCore.QPointF(size.width() / 2, size.height() / 2), size)
            painter.setOpacity(0.24)
            painter.drawPixmap(target, self.logo, QtCore.QRectF(self.logo.rect()))
            painter.setOpacity(1)
        if self.busy:
            ring = QtCore.QRectF(center.x() - 62, center.y() - 62, 124, 124)
            painter.setPen(QtGui.QPen(QtGui.QColor("#eadbd1"), 4))
            painter.drawEllipse(ring)
            pen = QtGui.QPen(QtGui.QColor("#e4772f"), 4)
            pen.setCapStyle(QtCore.Qt.RoundCap)
            painter.setPen(pen)
            angle = -(self.clock.elapsed() % 1400) * 5760 // 1400
            painter.drawArc(ring, angle, 95 * 16)


class Preview(QtWidgets.QWidget):
    changed = QtCore.Signal(float)

    def __init__(self):
        super().__init__()
        self.images = ()
        self.message = "Belum ada hasil"
        self.zoom = 1.0
        self.pan = QtCore.QPointF()
        self.drag = None
        self.split = 0.5
        self.tone = False
        self.rotation = 0.0
        self.focus = ()
        self.vertical_guide = False
        self.animation = QtCore.QVariantAnimation(self)
        self.animation.setDuration(160)
        self.animation.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self.animation.valueChanged.connect(self.apply_zoom)
        self.zoom_target = 1.0
        self.anchor = QtCore.QPointF()
        self.setMinimumHeight(220)

    def show_images(self, images):
        self.images = images
        self.animation.stop()
        self.zoom = self.zoom_target = 1.0
        self.pan = QtCore.QPointF()
        self.changed.emit(self.zoom)
        self.setCursor(QtCore.Qt.OpenHandCursor if images else QtCore.Qt.ArrowCursor)
        self.update()

    def set_zoom(self, target, anchor=None):
        if not self.images:
            return
        self.zoom_target = max(0.5, min(10.0, target))
        self.anchor = anchor if anchor is not None else QtCore.QPointF()
        self.animation.stop()
        self.animation.setStartValue(self.zoom)
        self.animation.setEndValue(self.zoom_target)
        self.animation.start()

    def apply_zoom(self, value):
        self.pan = self.anchor + (self.pan - self.anchor) * (value / self.zoom)
        self.zoom = value
        self.changed.emit(value)
        self.update()

    def fit(self):
        self.animation.stop()
        self.zoom = self.zoom_target = 1.0
        self.pan = QtCore.QPointF()
        self.changed.emit(self.zoom)
        self.update()

    def set_rotation(self, angle):
        self.rotation = angle
        self.update()

    def image_geometry(self):
        rect = QtCore.QRectF(self.rect())
        image = self.images[0]
        bounds = QtGui.QTransform().rotate(self.rotation).mapRect(QtCore.QRectF(image.rect()))
        scale = min((rect.width() - 24) / bounds.width(), (rect.height() - 24) / bounds.height()) * self.zoom
        width, height = image.width() * scale, image.height() * scale
        center = rect.center() + self.pan
        transform = QtGui.QTransform().translate(center.x(), center.y()).rotate(self.rotation)
        return transform, QtCore.QRectF(-width / 2, -height / 2, width, height)

    @staticmethod
    def image_point(point, target):
        x, y = point
        return QtCore.QPointF(target.left() + x * target.width(), target.top() + y * target.height())

    def wheelEvent(self, event):
        if self.images:
            anchor = QtCore.QPointF(event.position()) - QtCore.QPointF(self.rect().center())
            self.set_zoom(self.zoom_target * 1.18 ** (event.angleDelta().y() / 120), anchor)
            event.accept()

    def mousePressEvent(self, event):
        if self.images and event.button() == QtCore.Qt.LeftButton:
            self.animation.stop()
            self.zoom_target = self.zoom
            self.drag = event.localPos()
            self.setCursor(QtCore.Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.drag is not None:
            self.pan += event.localPos() - self.drag
            self.drag = event.localPos()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.drag is not None and event.button() == QtCore.Qt.LeftButton:
            self.drag = None
            self.setCursor(QtCore.Qt.OpenHandCursor)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
        rect = QtCore.QRectF(self.rect())
        clip = QtGui.QPainterPath()
        clip.addRoundedRect(rect, 16, 16)
        painter.setClipPath(clip)
        painter.fillRect(rect, QtGui.QColor("#132324"))
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        painter.setPen(QtGui.QColor("#223638"))
        for x in range(0, self.width(), 24):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 24):
            painter.drawLine(0, y, self.width(), y)
        if not self.images:
            painter.setPen(QtGui.QColor("#d5e5e4"))
            painter.drawText(self.rect(), QtCore.Qt.AlignCenter, self.message)
            return
        before, after = self.images
        transform, target = self.image_geometry()
        split = rect.width() * self.split
        if self.tone:
            self.draw_image(painter, after, transform, target)
        else:
            for image, start, width in ((before, 0, split), (after, split, rect.width() - split)):
                painter.save()
                painter.setClipRect(QtCore.QRectF(start, 0, width, rect.height()), QtCore.Qt.IntersectClip)
                self.draw_image(painter, image, transform, target)
                painter.restore()
            if 0 < split < rect.width():
                painter.setPen(QtGui.QColor("#e7fff4"))
                painter.drawLine(QtCore.QLineF(split, 0, split, rect.height()))
        if self.vertical_guide:
            pen = QtGui.QPen(QtGui.QColor("#b3efdb"), 1, QtCore.Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(QtCore.QLineF(rect.center().x(), 0, rect.center().x(), rect.height()))

    def draw_image(self, painter, image, transform, target):
        painter.save()
        painter.setWorldTransform(transform)
        if self.focus:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setClipPath(focus_path(self.focus, target), QtCore.Qt.IntersectClip)
        painter.drawPixmap(target, image, QtCore.QRectF(image.rect()))
        painter.restore()


class FocusPreview(Preview):
    selection_changed = QtCore.Signal()

    def __init__(self, images, angle, points):
        super().__init__()
        self.show_images(images)
        self.set_rotation(angle)
        self.tone = True
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.selection = QtCore.QRectF()
        self.original_points = points
        self.drag = None
        if points:
            transform, target = self.image_geometry()
            polygon = QtGui.QPolygonF([transform.map(self.image_point(point, target)) for point in points])
            rect = polygon.boundingRect().intersected(transform.mapRect(target))
            self.selection = QtCore.QRectF(self.normalized_position(rect.topLeft()),
                                          self.normalized_position(rect.bottomRight()))

    def display_bounds(self):
        transform, target = self.image_geometry()
        return transform.mapRect(target)

    def normalized_position(self, position):
        bounds = self.display_bounds()
        return QtCore.QPointF(
            max(0, min(1, (position.x() - bounds.left()) / bounds.width())),
            max(0, min(1, (position.y() - bounds.top()) / bounds.height())),
        )

    def screen_selection(self):
        bounds = self.display_bounds()
        return QtCore.QRectF(self.image_point((self.selection.left(), self.selection.top()), bounds),
                             self.image_point((self.selection.right(), self.selection.bottom()), bounds))

    def focus_points(self):
        if self.original_points is not None:
            return self.original_points
        if self.selection.isEmpty():
            return ()
        transform, target = self.image_geometry()
        inverse, _ = transform.inverted()
        rect = self.screen_selection()
        corners = (rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft())
        return tuple(((point.x() - target.left()) / target.width(),
                      (point.y() - target.top()) / target.height()) for point in map(inverse.map, corners))

    def edges_at(self, position):
        if self.selection.isEmpty():
            return 0, 0
        rect = self.screen_selection()
        tolerance = 8
        if not rect.adjusted(-tolerance, -tolerance, tolerance, tolerance).contains(position):
            return 0, 0
        left, right = abs(position.x() - rect.left()), abs(position.x() - rect.right())
        top, bottom = abs(position.y() - rect.top()), abs(position.y() - rect.bottom())
        horizontal = (-1 if left <= right else 1) if min(left, right) <= tolerance else 0
        vertical = (-1 if top <= bottom else 1) if min(top, bottom) <= tolerance else 0
        return horizontal, vertical

    def update_cursor(self, horizontal, vertical):
        cursors = {
            (0, 0): QtCore.Qt.CrossCursor,
            (1, 0): QtCore.Qt.SizeHorCursor,
            (0, 1): QtCore.Qt.SizeVerCursor,
            (1, 1): QtCore.Qt.SizeFDiagCursor,
            (-1, 1): QtCore.Qt.SizeBDiagCursor,
        }
        direction = (horizontal * vertical, 1) if horizontal and vertical else (abs(horizontal), abs(vertical))
        self.setCursor(cursors[direction])

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return
        position = event.localPos()
        horizontal, vertical = self.edges_at(position)
        if horizontal or vertical or self.display_bounds().contains(position):
            self.drag = (self.normalized_position(position), QtCore.QRectF(self.selection), horizontal, vertical)
            self.update_cursor(horizontal, vertical)

    def mouseMoveEvent(self, event):
        if self.drag is None:
            self.update_cursor(*self.edges_at(event.localPos()))
        elif event.buttons() & QtCore.Qt.LeftButton:
            self.resize_selection(event.localPos())

    def resize_selection(self, position):
        origin, previous, horizontal, vertical = self.drag
        point = self.normalized_position(position)
        if not horizontal and not vertical:
            self.selection = QtCore.QRectF(origin, point).normalized()
        else:
            rect = QtCore.QRectF(previous)
            delta = point - origin
            minimum_width = min(0.005, previous.width())
            minimum_height = min(0.005, previous.height())
            if horizontal < 0:
                rect.setLeft(max(0, min(rect.right() - minimum_width, rect.left() + delta.x())))
            elif horizontal > 0:
                rect.setRight(min(1, max(rect.left() + minimum_width, rect.right() + delta.x())))
            if vertical < 0:
                rect.setTop(max(0, min(rect.bottom() - minimum_height, rect.top() + delta.y())))
            elif vertical > 0:
                rect.setBottom(min(1, max(rect.top() + minimum_height, rect.bottom() + delta.y())))
            self.selection = rect
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.drag is not None:
            _, previous, horizontal, vertical = self.drag
            self.resize_selection(event.localPos())
            if not horizontal and not vertical and min(self.selection.width(), self.selection.height()) < 0.005:
                self.selection = previous
            if self.selection != previous:
                self.original_points = None
            self.drag = None
            self.update_cursor(*self.edges_at(event.localPos()))
            self.selection_changed.emit()
            self.update()

    def reset_selection(self):
        self.selection = QtCore.QRectF()
        self.original_points = None
        self.selection_changed.emit()
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.selection.isEmpty():
            return
        rect = self.screen_selection()
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ffd38a"), 2))
        shade = QtGui.QPainterPath()
        shade.addRect(self.display_bounds())
        shade.addRect(rect)
        painter.fillPath(shade, QtGui.QColor(12, 28, 31, 145))
        painter.drawRect(rect)


class FocusDialog(QtWidgets.QDialog):
    def __init__(self, parent, images, angle, points):
        super().__init__(parent)
        self.setObjectName("rotationDialog")
        self.setWindowTitle(tr("Fokus Rak"))
        self.resize(660, 630)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(label("Fokus Rak", "heading"))
        hint = label("Klik dan seret untuk membuat pembatas persegi panjang. Seret sisi atau sudut untuk mengubah ukurannya.", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.preview = FocusPreview(images, angle, points)
        layout.addWidget(self.preview, 1)
        actions = QtWidgets.QHBoxLayout()
        self.reset_button = button("Reset pembatas", self.preview.reset_selection, "Kembalikan tampilan ke seluruh gambar")
        actions.addWidget(self.reset_button)
        actions.addStretch()
        actions.addWidget(button("Batal", self.reject))
        apply_button = button("Terapkan", self.accept)
        apply_button.setObjectName("primary")
        actions.addWidget(apply_button)
        layout.addLayout(actions)
        self.preview.selection_changed.connect(self.update_actions)
        self.update_actions()

    def update_actions(self):
        self.reset_button.setEnabled(not self.preview.selection.isEmpty())


class RotationDialog(QtWidgets.QDialog):
    def __init__(self, parent, images, angle, original, enhanced):
        super().__init__(parent)
        self.original = original
        self.enhanced = enhanced
        self.results = queue.Queue()
        self.target = None
        self.setObjectName("rotationDialog")
        self.setWindowTitle(tr("Koreksi Geometrik"))
        self.resize(660, 630)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.addWidget(label("Koreksi Geometrik", "heading"))
        hint = label("Putar seluruh gambar dengan bantuan garis vertikal. Sudut positif memutar searah jarum jam.", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.preview = Preview()
        self.preview.tone = True
        self.preview.vertical_guide = True
        self.preview.show_images(images)
        layout.addWidget(self.preview, 1)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(label("Sudut rotasi", "section"))
        self.angle = QtWidgets.QDoubleSpinBox()
        self.angle.setAccessibleName(tr("Sudut rotasi"))
        self.angle.setRange(-180, 180)
        self.angle.setDecimals(1)
        self.angle.setSingleStep(0.5)
        self.angle.setSuffix("°")
        self.angle.setValue(angle)
        self.angle.valueChanged.connect(self.update_preview)
        controls.addWidget(self.angle)
        self.reset_angle = button("Reset sudut", lambda: self.angle.setValue(0))
        controls.addWidget(self.reset_angle)
        controls.addStretch()
        self.original_view = QtWidgets.QCheckBox(tr("Lihat sebelum rotasi"))
        self.original_view.toggled.connect(self.update_preview)
        controls.addWidget(self.original_view)
        layout.addLayout(controls)

        self.status = label("PNG koreksi disimpan sebagai salinan visual.", "muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QtWidgets.QHBoxLayout()
        self.save_button = button("Simpan PNG…", self.choose_export)
        actions.addWidget(self.save_button)
        actions.addStretch()
        self.cancel_button = button("Batal", self.reject)
        actions.addWidget(self.cancel_button)
        self.apply_button = button("Terapkan ke pratinjau", self.accept)
        self.apply_button.setObjectName("primary")
        actions.addWidget(self.apply_button)
        for control in (self.reset_angle, self.save_button, self.cancel_button, self.apply_button):
            control.setAutoDefault(False)
        layout.addLayout(actions)
        self.export_timer = QtCore.QTimer(self)
        self.export_timer.setInterval(50)
        self.export_timer.timeout.connect(self.finish_export)
        self.update_preview()

    def update_preview(self):
        self.preview.set_rotation(0 if self.original_view.isChecked() else self.angle.value())
        self.save_button.setEnabled(self.angle.value() != 0)

    def choose_export(self):
        picker = QtWidgets.QFileDialog(self, tr("Simpan PNG koreksi"), str(self.enhanced.parent), "PNG (*.png)")
        picker.setAcceptMode(QtWidgets.QFileDialog.AcceptSave)
        picker.setDefaultSuffix("png")
        picker.selectFile("orthomosaic_koreksi.png")
        if picker.exec_() == QtWidgets.QDialog.Accepted:
            self.start_export(Path(picker.selectedFiles()[0]))
        picker.deleteLater()

    def start_export(self, target):
        if self.target is not None or self.angle.value() == 0:
            return
        if target.suffix.lower() != ".png":
            self.status.setText("Gunakan ekstensi .png untuk salinan koreksi.")
            return
        if target.resolve() in (self.original.resolve(), self.enhanced.resolve()):
            self.status.setText("Pilih nama lain agar berkas sumber dan hasil Auto Tone tetap tersedia.")
            return
        self.target = target
        self.status.setText("Menyimpan PNG koreksi…")
        self.set_controls_enabled(False)
        threading.Thread(target=export_rotated_png,
                         args=(self.enhanced, target, self.angle.value(), self.results), daemon=True).start()
        self.export_timer.start()

    def set_controls_enabled(self, enabled):
        for control in (self.angle, self.reset_angle, self.original_view, self.save_button,
                        self.cancel_button, self.apply_button):
            control.setEnabled(enabled)

    def finish_export(self):
        try:
            error = self.results.get_nowait()
        except queue.Empty:
            return
        self.export_timer.stop()
        target, self.target = self.target, None
        self.set_controls_enabled(True)
        self.status.setText(f"PNG koreksi tersimpan · {target.name}" if error is None else "PNG koreksi gagal disimpan.")
        if error is not None:
            QtWidgets.QMessageBox.warning(self, tr("Koreksi Geometrik"), tr(error))

    def reject(self):
        if self.target is None:
            super().reject()


class Window(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.jobs = []
        self.current = None
        self.active = None
        self.process = None
        self.command = "run"
        self.destination = None
        self.records = []
        self.engine_records = []
        self.stage_index = 0
        self.follow = True
        self.active_stage = 0
        self.preview_requests = queue.Queue()
        self.preview_worker = None
        self.preview_pending = None
        self.preview_generation = 0
        self.output_export = None
        self.output_export_results = queue.Queue()
        self.output_export_timer = QtCore.QTimer(self)
        self.output_export_timer.setInterval(60)
        self.output_export_timer.timeout.connect(self.poll_output_export)
        self.setWindowTitle(COMPANY_NAME)
        self.setWindowIcon(QtGui.QIcon(str(BASE / "logo-dinar.png")))
        self.setMinimumSize(1060, 730)
        self.resize(1362, 933)
        body = QtWidgets.QWidget()
        body.setObjectName("body")
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(body)
        header = QtWidgets.QWidget()
        header.setObjectName("header")
        top = QtWidgets.QHBoxLayout(header)
        top.setContentsMargins(16, 6, 16, 6)
        top.setSpacing(12)
        logo = label("", "headerLogo")
        logo.setAccessibleName("Logo Coral Marine Photogrammetry")
        logo_source = QtGui.QPixmap(str(BASE / "logo-cmp.png"))
        if not logo_source.isNull():
            logo_image = logo_source.scaled(132, 132, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            logo_image.setDevicePixelRatio(2)
            logo.setPixmap(logo_image)
            top.addWidget(logo, 0, QtCore.Qt.AlignVCenter)
        brand = QtWidgets.QVBoxLayout()
        brand.setSpacing(2)
        brand.addWidget(label(APP_TITLE, "title"))
        brand.addWidget(label(APP_DESCRIPTION, "description"))
        top.addLayout(brand, 1)
        self.language_picker = AccuracyPicker()
        self.language_picker.addItem("Indonesia", "id")
        self.language_picker.addItem("English", "en")
        self.language_picker.setAccessibleName("Bahasa / Language")
        self.language_picker.setToolTip("Bahasa / Language")
        top.addWidget(self.language_picker, 0, QtCore.Qt.AlignVCenter)
        self.guide_button = button("Panduan", self.show_guide, "Buka panduan penggunaan")
        top.addWidget(self.guide_button, 0, QtCore.Qt.AlignVCenter)
        self.badge = label("Menunggu input", "badge")
        top.addWidget(self.badge, 0, QtCore.Qt.AlignVCenter)
        layout.addWidget(header)

        columns = QtWidgets.QGridLayout()
        columns.setContentsMargins(14, 10, 14, 10)
        columns.setSpacing(12)
        columns.setRowStretch(0, 1)
        for column, stretch in enumerate((7, 10, 7)):
            columns.setColumnStretch(column, stretch)
        columns.setColumnMinimumWidth(0, 330)
        columns.setColumnMinimumWidth(2, 330)
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        setup, inputs = panel("Siapkan Input")
        inputs.setContentsMargins(14, 10, 14, 10)
        inputs.setSpacing(5)
        inputs.addWidget(label("Sumber media", "section"))
        self.input_label = ElidedLabel()
        self.input_label.setFixedHeight(28)
        pickers = QtWidgets.QHBoxLayout()
        self.video_button = button("Video…", self.pick_video, "Pilih video, TIFF, atau PNG")
        self.frames_button = button("Foto Frames…", self.pick_frames, "Pilih minimal tiga foto yang saling bertumpang tindih")
        pickers.addWidget(self.video_button)
        pickers.addWidget(self.frames_button)
        inputs.addLayout(pickers)
        self.queue_button = button("Antrean", self.show_queue)
        selection_row = QtWidgets.QHBoxLayout()
        selection_row.addWidget(self.input_label, 1)
        selection_row.addWidget(self.queue_button)
        inputs.addLayout(selection_row)
        inputs.addSpacing(1)
        inputs.addWidget(label("Pengaturan hasil", "section"))
        folder_row = QtWidgets.QHBoxLayout()
        folder_row.addWidget(label("Folder hasil"), 1)
        self.folder_button = button("Ubah…", self.pick_destination)
        self.folder_button.setMinimumWidth(102)
        folder_row.addWidget(self.folder_button)
        inputs.addLayout(folder_row)
        self.path_label = ElidedLabel()
        self.path_label.setFixedHeight(24)
        inputs.addWidget(self.path_label)
        self.accuracy = AccuracyPicker()
        self.accuracy.addItems(list(ACCURACY_DOWNSCALE))
        self.accuracy.setCurrentText("Medium")
        self.accuracy_row = QtWidgets.QWidget()
        accuracy_row = QtWidgets.QHBoxLayout(self.accuracy_row)
        accuracy_row.setContentsMargins(0, 0, 0, 0)
        accuracy_row.addWidget(label("Akurasi pencocokan"), 1)
        accuracy_row.addWidget(self.accuracy)
        inputs.addWidget(self.accuracy_row)
        inputs.addSpacing(4)
        controls = QtWidgets.QGridLayout()
        controls.setSpacing(5)
        self.start_button = button("Mulai", self.start_batch, "Mulai pemrosesan")
        self.start_button.setObjectName("primary")
        self.pause_button = button("Jeda", self.toggle_pause)
        self.stop_button = button("Berhenti", self.stop, "Hentikan antrean di batas tahap")
        self.reset_button = button("Reset", self.reset, "Kosongkan sesi pemrosesan")
        self.resume_icon = action_icon("play")
        self.pause_icon = action_icon("pause")
        icons = (action_icon("play", "#ffffff"), self.pause_icon, action_icon("stop"), action_icon("reset"))
        for index, control in enumerate((self.start_button, self.pause_button, self.stop_button, self.reset_button)):
            control.setIcon(icons[index])
            control.setIconSize(QtCore.QSize(15, 15))
            if index == 0:
                controls.addWidget(control, 0, 0, 1, 3)
            else:
                controls.addWidget(control, 1, index - 1)
                controls.setColumnStretch(index - 1, 1)
        inputs.addLayout(controls)
        self.control_hint = ElidedLabel("muted", QtCore.Qt.ElideRight)
        self.control_hint.setFixedHeight(20)
        inputs.addWidget(self.control_hint)
        left.addWidget(setup)
        self.flow = label("Video", "flow")
        timeline, steps = panel("Tahapan Pemrosesan", self.flow)
        steps.setContentsMargins(14, 10, 14, 10)
        self.timeline_intro = label("Pilih sumber media untuk melihat tahapan pemrosesan yang sesuai.", "muted")
        self.timeline_intro.setWordWrap(True)
        steps.addWidget(self.timeline_intro)
        self.timeline_content = QtWidgets.QWidget()
        timeline_content = QtWidgets.QVBoxLayout(self.timeline_content)
        timeline_content.setContentsMargins(0, 0, 0, 0)
        timeline_content.setSpacing(3)
        steps.addWidget(self.timeline_content, 1)
        self.total_text = label("0/8 tahap · 0%", "metric")
        self.status = label("Menunggu input.", "muted")
        self.status.setWordWrap(True)
        self.status.setFixedHeight(34)
        self.total = ProgressBar()
        self.total.setFixedHeight(10)
        progress_heading = QtWidgets.QHBoxLayout()
        progress_heading.addWidget(label("Progres total", "section"), 1)
        progress_heading.addWidget(self.total_text)
        timeline_content.addLayout(progress_heading)
        timeline_content.addWidget(self.total)
        progress_note = label("Persentase menunjukkan progres tahap.", "muted")
        progress_note.setToolTip("Durasi menunjukkan waktu yang sudah dipakai oleh tahap yang selesai.")
        timeline_content.addWidget(progress_note)
        timeline_content.addWidget(self.status)
        stage_heading = QtWidgets.QHBoxLayout()
        stage_heading.addWidget(label("Rincian tahapan", "section"), 1)
        stage_heading.addWidget(label("Durasi (dtk)", "metric"))
        timeline_content.addLayout(stage_heading)
        self.stage_rows = []
        stage_area = QtWidgets.QWidget()
        stage_layout = QtWidgets.QHBoxLayout(stage_area)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(5)
        self.timeline_rail = TimelineRail(self.stage_rows)
        stage_layout.addWidget(self.timeline_rail)
        rows = QtWidgets.QVBoxLayout()
        rows.setSpacing(3)
        for _ in VIDEO_STAGES:
            line = QtWidgets.QWidget()
            row = QtWidgets.QHBoxLayout(line)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(3)
            name = ElidedLabel("muted", QtCore.Qt.ElideRight)
            row.addWidget(name, 5)
            bar = ProgressBar()
            bar.setMinimumWidth(36)
            bar.setMaximumWidth(85)
            bar.setFixedHeight(6)
            row.addWidget(bar, 1)
            detail = label("", "stageState")
            detail.setFixedWidth(98)
            detail.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            row.addWidget(detail)
            self.stage_rows.append((line, name, bar, detail))
            rows.addStretch()
            rows.addWidget(line)
        rows.addStretch()
        stage_layout.addLayout(rows, 1)
        timeline_content.addWidget(stage_area, 1)
        self.timeline_details = label("", "muted")
        self.timeline_details.setFixedHeight(34)
        self.timeline_details.setWordWrap(True)
        timeline_content.addWidget(self.timeline_details)
        left.addWidget(timeline)
        left.addStretch()
        self.sidebar_layout = left
        sidebar = QtWidgets.QWidget()
        sidebar.setObjectName("sidebarContent")
        left.setContentsMargins(0, 0, 0, 0)
        sidebar.setLayout(left)
        sidebar_scroll = QtWidgets.QScrollArea()
        sidebar_scroll.setObjectName("sidebar")
        sidebar_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        sidebar_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setWidget(sidebar)
        columns.addWidget(sidebar_scroll, 0, 0)

        result, viewer = panel("Pratinjau Hasil")
        self.result_navigation = QtWidgets.QWidget()
        result_navigation = QtWidgets.QVBoxLayout(self.result_navigation)
        result_navigation.setContentsMargins(0, 0, 0, 0)
        result_navigation.setSpacing(6)
        result_navigation.addWidget(label("Hasil terpilih", "section"))
        results = QtWidgets.QHBoxLayout()
        self.previous_result = button("‹", lambda: self.move_result(-1), "Hasil sebelumnya")
        self.next_result = button("›", lambda: self.move_result(1), "Hasil berikutnya")
        self.result_label = ElidedLabel()
        self.result_label.setFixedHeight(26)
        results.addWidget(self.previous_result)
        results.addWidget(self.next_result)
        results.addWidget(self.result_label, 1)
        result_navigation.addLayout(results)
        viewer.addWidget(self.result_navigation)
        self.image_controls = QtWidgets.QWidget()
        image_controls = QtWidgets.QVBoxLayout(self.image_controls)
        image_controls.setContentsMargins(0, 0, 0, 0)
        image_controls.setSpacing(6)
        preview_heading = QtWidgets.QHBoxLayout()
        preview_heading.addWidget(label("Tampilan gambar", "section"))
        self.preview_hint = ElidedLabel("muted", QtCore.Qt.ElideRight)
        self.preview_hint.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        preview_heading.addWidget(self.preview_hint, 1)
        image_controls.addLayout(preview_heading)
        self.preview_tools = QtWidgets.QGridLayout()
        self.preview_tools.setSpacing(4)
        self.preview_tools.setColumnStretch(0, 1)
        self.zoom_tools = QtWidgets.QWidget()
        toolbar = QtWidgets.QHBoxLayout(self.zoom_tools)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)
        self.preview = Preview()
        self.zoom_out = button("−", lambda: self.preview.set_zoom(self.preview.zoom_target / 1.35), "Perkecil gambar")
        self.zoom_in = button("+", lambda: self.preview.set_zoom(self.preview.zoom_target * 1.35), "Perbesar gambar")
        self.fit_button = button("Pas", self.preview.fit, "Paskan gambar ke panel")
        for control, width in ((self.zoom_out, 32), (self.zoom_in, 32), (self.fit_button, 43)):
            control.setObjectName("zoomButton")
            control.setFixedWidth(width)
            toolbar.addWidget(control)
        self.zoom_label = label("100%", "metric")
        self.zoom_label.setFixedWidth(43)
        self.preview.changed.connect(self.update_zoom_controls)
        toolbar.addWidget(self.zoom_label)
        self.image_tools = QtWidgets.QWidget()
        image_tools = QtWidgets.QGridLayout(self.image_tools)
        self.image_tools_layout = image_tools
        image_tools.setContentsMargins(0, 0, 0, 0)
        image_tools.setSpacing(4)
        self.tone_button = button("Auto Tone", self.toggle_tone)
        self.tone_button.ensurePolished()
        tone_width = self.tone_button.sizeHint().width()
        self.tone_button.setText("Bandingkan")
        self.tone_button.setFixedWidth(max(tone_width, self.tone_button.sizeHint().width()))
        self.update_tone_button()
        image_tools.addWidget(self.tone_button, 0, 0)
        self.focus_button = button("Fokus Rak…", self.show_focus, "Sembunyikan area di luar pembatas rak")
        image_tools.addWidget(self.focus_button, 0, 1)
        self.rotation_button = button("Koreksi Geometrik", self.show_rotation,
                                      "Putar orthomosaic dan simpan salinan PNG")
        self.reflow_preview_tools()
        image_controls.addLayout(self.preview_tools)
        viewer.addWidget(self.image_controls)
        self.preview_stack = QtWidgets.QStackedWidget()
        self.preview_empty = self.build_empty_preview(logo_source)
        self.preview_stack.addWidget(self.preview_empty)
        self.preview_stack.addWidget(self.preview)
        viewer.addWidget(self.preview_stack, 1)
        self.compare_footer = QtWidgets.QWidget()
        compare_controls = QtWidgets.QWidget()
        compare = QtWidgets.QHBoxLayout(compare_controls)
        compare.setContentsMargins(0, 0, 0, 0)
        compare.addWidget(label("Sebelum", "muted"))
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(50)
        self.slider.setAccessibleName("Pembanding sebelum dan sesudah Auto Tone")
        self.slider.setToolTip("Geser untuk membandingkan gambar asli dan hasil Auto Tone.")
        self.slider.valueChanged.connect(self.set_split)
        compare.addWidget(self.slider, 1)
        compare.addWidget(label("Sesudah", "muted"))
        self.compare_views = QtWidgets.QStackedLayout(self.compare_footer)
        self.compare_views.setContentsMargins(0, 0, 0, 0)
        self.compare_views.addWidget(compare_controls)
        self.compare_views.addWidget(label("Hasil Auto Tone", "muted"))
        viewer.addWidget(self.compare_footer)
        columns.addWidget(result, 0, 1)

        iteration, detail = panel("Rincian Proses")
        detail.addWidget(label("Log tahapan", "section"))
        self.stage_label = label("", "muted")
        detail.addWidget(self.stage_label)
        self.log_navigation = QtWidgets.QWidget()
        navigation = QtWidgets.QHBoxLayout(self.log_navigation)
        navigation.setContentsMargins(0, 0, 0, 0)
        self.previous_stage = button("‹", lambda: self.move_stage(-1), "Tahap sebelumnya")
        self.next_stage = button("›", lambda: self.move_stage(1), "Tahap berikutnya")
        self.follow_button = button("Ikuti proses", self.follow_stage)
        for control in (self.previous_stage, self.next_stage, self.follow_button):
            navigation.addWidget(control)
        navigation.addStretch()
        detail.addWidget(self.log_navigation)
        detail.addWidget(divider())
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log_stack = QtWidgets.QStackedWidget()
        log_intro = QtWidgets.QWidget()
        intro = QtWidgets.QVBoxLayout(log_intro)
        intro.setContentsMargins(0, 5, 0, 0)
        intro.addWidget(label("Apa yang ditampilkan?", "section"))
        explanation = label("Catatan tiap tahap muncul saat pekerjaan berjalan. "
                            "Gunakan panah untuk meninjau tahap, atau Ikuti proses untuk kembali ke tahap aktif.", "muted")
        explanation.setWordWrap(True)
        intro.addWidget(explanation)
        intro.addSpacing(6)
        example = label("Contoh alur video\nEkstraksi frame → Cocokkan foto → Ekspor orthomosaic", "muted")
        example.setWordWrap(True)
        intro.addWidget(example)
        intro.addStretch()
        self.log_stack.addWidget(log_intro)
        self.log_stack.addWidget(self.log)
        detail.addWidget(self.log_stack, 1)
        detail.addWidget(divider())
        detail.addSpacing(4)
        detail.addWidget(label("Berkas hasil", "section"))
        actions = QtWidgets.QGridLayout()
        actions.setHorizontalSpacing(10)
        actions.setVerticalSpacing(9)
        self.result_buttons = {}
        for index, (key, title) in enumerate((("folder", "Folder hasil"), ("log", "Buka log"),
                                              ("original", "Gambar asli"), ("enhanced", "PNG hasil"))):
            control = button(title, lambda checked=False, key=key: self.open_result(key))
            control.setMinimumHeight(35)
            actions.addWidget(control, index // 2, index % 2)
            self.result_buttons[key] = control
        detail.addLayout(actions)
        detail.addSpacing(8)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        columns.addWidget(iteration, 0, 2)
        layout.addLayout(columns, 1)
        footer = QtWidgets.QWidget()
        footer.setObjectName("footer")
        footer_row = QtWidgets.QGridLayout(footer)
        footer_row.setContentsMargins(16, 5, 16, 5)
        footer_row.setColumnStretch(0, 1)
        footer_row.setColumnStretch(2, 1)
        footer_row.addWidget(label("CMP v2.12", "muted"), 0, 0, QtCore.Qt.AlignLeft)
        footer_row.addWidget(label("Copyright © 2026 PT Dinar Darum Lestari", "muted"), 0, 1, QtCore.Qt.AlignHCenter)
        footer_row.addWidget(label("Agisoft Metashape Pro + FFmpeg", "muted"), 0, 2, QtCore.Qt.AlignRight)
        layout.addWidget(footer)
        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(350)
        self.poll_timer.timeout.connect(self.poll_job)
        self.preview_timer = QtCore.QTimer(self)
        self.preview_timer.setInterval(40)
        self.preview_timer.timeout.connect(self.poll_preview)
        self.configure_job(None)
        self.update_controls()
        self.language_picker.currentIndexChanged.connect(self.change_language)

    def change_language(self, index):
        QtWidgets.QApplication.instance().setProperty("cmpLanguage", self.language_picker.itemData(index))
        for widget in self.findChildren(QtWidgets.QWidget):
            if isinstance(widget, (LocalizedText, ElidedLabel)):
                widget.retranslate()
        self.update_tone_width()
        self.update_controls()
        self.show_iteration()
        self.slider.setAccessibleName(tr("Pembanding sebelum dan sesudah Auto Tone"))
        self.slider.setToolTip(tr("Geser untuk membandingkan gambar asli dan hasil Auto Tone."))

    def update_tone_width(self):
        metrics = self.tone_button.fontMetrics()
        width = max(metrics.horizontalAdvance(tr(text)) for text in ("Auto Tone", "Bandingkan"))
        self.tone_button.setFixedWidth(width + 28)

    def toggle_tone(self):
        self.preview.tone = not self.preview.tone
        self.update_tone_button()
        self.compare_views.setCurrentIndex(int(self.preview.tone))
        self.slider.setEnabled(not self.preview.tone)
        self.preview.update()

    def build_empty_preview(self, logo_image):
        empty = QtWidgets.QFrame()
        empty.setObjectName("previewEmpty")
        layout = QtWidgets.QVBoxLayout(empty)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addStretch()
        self.preview_mark = PreviewMark(logo_image)
        layout.addWidget(self.preview_mark, 0, QtCore.Qt.AlignHCenter)
        self.empty_title = label("", "emptyHeading")
        self.empty_title.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_title.setWordWrap(True)
        layout.addWidget(self.empty_title)
        self.empty_detail = label("", "muted")
        self.empty_detail.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_detail.setWordWrap(True)
        layout.addWidget(self.empty_detail)
        layout.addStretch()
        return empty

    def show_empty_preview(self, title, detail, busy=False):
        self.empty_title.setText(title)
        self.empty_detail.setText(detail)
        self.preview_mark.set_busy(busy)
        self.image_controls.hide()
        self.compare_footer.hide()
        self.preview_stack.setCurrentWidget(self.preview_empty)

    @staticmethod
    def preview_layout_mode(width):
        return (width < 1180, width < 1520)

    def reflow_preview_tools(self):
        compact, separate = self.preview_layout_mode(self.width())
        if compact:
            self.image_tools_layout.addWidget(self.rotation_button, 1, 0, 1, 2, QtCore.Qt.AlignRight)
        else:
            self.image_tools_layout.addWidget(self.rotation_button, 0, 2)
        if separate:
            self.preview_tools.addWidget(self.zoom_tools, 0, 0, 1, 2, QtCore.Qt.AlignLeft)
            self.preview_tools.addWidget(self.image_tools, 1, 0, 1, 2, QtCore.Qt.AlignRight)
        else:
            self.preview_tools.addWidget(self.zoom_tools, 0, 0, QtCore.Qt.AlignLeft)
            self.preview_tools.addWidget(self.image_tools, 0, 1, QtCore.Qt.AlignRight)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if (hasattr(self, "rotation_button") and
                self.preview_layout_mode(event.size().width()) != self.preview_layout_mode(event.oldSize().width())):
            self.reflow_preview_tools()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.WindowStateChange and hasattr(self, "preview_mark"):
            self.preview_mark.refresh_timer()

    def show_rotation(self):
        if not self.preview.images or self.current is None:
            return
        job = self.jobs[self.current]
        dialog = RotationDialog(self, self.preview.images, job.rotation,
                                self.result_path("original"), self.result_path("enhanced"))
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            job.rotation = dialog.angle.value()
            self.preview.set_rotation(job.rotation)
            self.preview.fit()
        dialog.deleteLater()

    def show_focus(self):
        if not self.preview.images or self.current is None:
            return
        job = self.jobs[self.current]
        dialog = FocusDialog(self, self.preview.images, job.rotation, job.focus)
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            job.focus = dialog.preview.focus_points()
            self.preview.focus = job.focus
            self.preview.update()
        dialog.deleteLater()

    def update_tone_button(self):
        if self.preview.tone:
            text, tooltip = "Bandingkan", "Tampilkan pembanding sebelum dan sesudah"
        else:
            text, tooltip = "Auto Tone", "Tampilkan gambar hasil Auto Tone"
        self.tone_button.setText(text)
        self.tone_button.setToolTip(tooltip)
        self.tone_button.setAccessibleName(tooltip)

    def update_zoom_controls(self, zoom):
        self.zoom_label.setText(f"{zoom:.0%}")
        available = bool(self.preview.images)
        self.zoom_out.setEnabled(available and self.preview.zoom_target > 0.5)
        self.zoom_in.setEnabled(available and self.preview.zoom_target < 10.0)
        self.fit_button.setEnabled(available)

    def set_split(self, value):
        self.preview.split = value / 100
        self.preview.update()

    def pick_video(self):
        videos = " ".join(f"*{ext}" for ext in sorted(VIDEO_TYPES))
        video_filter = f"Video ({videos})"
        mixed_filter = f"Video / TIFF / PNG ({videos} *.tif *.tiff *.png)"
        selected, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, tr("Pilih video atau gambar"), "", f"{video_filter};;{mixed_filter}",
        )
        self.set_sources(selected)

    def pick_frames(self):
        selected, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, tr("Pilih minimal 3 foto dari area yang sama"), "", tr("Foto frame") + " (*.jpg *.jpeg *.png *.tif *.tiff)",
        )
        self.set_sources(selected, frame_group=True)

    def set_sources(self, selected, frame_group=False):
        paths = tuple(dict.fromkeys(Path(path).absolute() for path in selected))
        if self.active is not None or not paths:
            return
        extensions = FRAME_TYPES if frame_group else INPUT_TYPES
        if frame_group and len(paths) < 3:
            self.warn("Pilih minimal 3 foto frame berbeda.")
            return
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in extensions:
                self.warn(f"File tidak valid atau tidak didukung.\n{path}")
                return
        self.jobs = [QueueEntry(paths[0], frames=paths)] if frame_group else [QueueEntry(path) for path in paths]
        self.configure_job(0)
        self.status.setText("Siap diproses.")
        self.set_badge("Siap diproses")
        self.update_controls()

    def warn(self, message):
        QtWidgets.QMessageBox.warning(self, APP_TITLE, tr(message))

    def show_guide(self):
        steps = (
            ("Pilih sumber media",
             "Klik Video untuk memilih satu atau beberapa video. Klik Foto Frames untuk memilih sedikitnya tiga foto "
             "yang saling bertumpang tindih."),
            ("Atur penyimpanan dan akurasi",
             "Klik Ubah untuk menentukan folder hasil. Jika tidak diubah, aplikasi memakai folder file sumber pertama. "
             "Untuk video atau Foto Frames, pilih akurasi pencocokan sesuai kebutuhan."),
            ("Mulai dan pantau pemrosesan",
             "Klik Mulai. File dalam antrean diproses berurutan. Lihat progres di Tahapan Pemrosesan dan detailnya "
             "di Log tahapan. Jeda dan Berhenti berlaku di batas tahap."),
            ("Tinjau hasil gambar",
             "Gunakan panah di Hasil terpilih atau tombol Antrean untuk memilih hasil. Roda mouse mengatur zoom "
             "dan seret untuk menggeser gambar. Gunakan Auto Tone atau Bandingkan untuk mengganti tampilan. "
             "Gunakan Koreksi Geometrik untuk memutar gambar, lalu Fokus Rak untuk membatasi area yang terlihat."),
            ("Buka berkas hasil",
             "Gunakan tombol Folder hasil, Buka log, Gambar asli, atau PNG hasil di bagian Berkas hasil. "
             "PNG hasil menyertakan koreksi yang diterapkan. Klik Reset untuk memulai sesi baru."),
        )
        dialog = QtWidgets.QDialog(self)
        dialog.setObjectName("guide")
        dialog.setWindowTitle(tr("Panduan Penggunaan"))
        dialog.setWindowModality(QtCore.Qt.WindowModal)
        dialog.resize(640, 610)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(label("Panduan Penggunaan", "heading"))
        layout.addWidget(divider())

        scroll = QtWidgets.QScrollArea()
        scroll.setObjectName("guideScroll")
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        content = QtWidgets.QWidget()
        content.setObjectName("guideContent")
        items = QtWidgets.QVBoxLayout(content)
        items.setContentsMargins(0, 1, 6, 0)
        items.setSpacing(10)
        for number, (title, description) in enumerate(steps, 1):
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(10)
            marker = label(str(number), "guideNumber")
            marker.setFixedSize(24, 24)
            marker.setAlignment(QtCore.Qt.AlignCenter)
            row.addWidget(marker, 0, QtCore.Qt.AlignTop)
            copy = QtWidgets.QVBoxLayout()
            copy.setSpacing(3)
            copy.addWidget(label(title, "section"))
            body = label(description, "muted")
            body.setWordWrap(True)
            copy.addWidget(body)
            row.addLayout(copy, 1)
            items.addLayout(row)
        items.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        close = button("Tutup", dialog.accept)
        close.setObjectName("primary")
        close.setMinimumWidth(92)
        close.setDefault(True)
        layout.addWidget(close, 0, QtCore.Qt.AlignRight)
        close.setFocus()
        dialog.exec_()
        dialog.deleteLater()

    def set_badge(self, message, phase=""):
        self.badge.setText(message)
        if self.badge.property("phase") != phase:
            self.badge.setProperty("phase", phase)
            self.badge.style().unpolish(self.badge)
            self.badge.style().polish(self.badge)

    def pick_destination(self):
        initial = self.output_root() or Path.home()
        path = QtWidgets.QFileDialog.getExistingDirectory(self, tr("Pilih folder hasil"), str(initial))
        if path:
            self.destination = Path(path)
            self.update_controls()

    def output_root(self):
        return self.destination or (self.jobs[0].source.parent if self.jobs else None)

    def reset(self):
        self.jobs.clear()
        self.configure_job(None)
        self.status.setText("Menunggu input.")
        self.set_badge("Menunggu input")
        self.update_controls()

    def configure_job(self, index, message="Belum ada hasil"):
        self.current = index
        job = self.jobs[index] if index is not None else None
        self.stages = IMAGE_STAGES if job and job.enhance_only else VIDEO_STAGES[1:] if job and job.frames else VIDEO_STAGES
        self.first_stage = 2 if job and job.frames else 1
        title = job.title if job else "Belum ada file yang dipilih."
        self.input_label.show_text(title)
        self.flow.setText("Foto frame" if job and job.frames else "Gambar" if job and job.enhance_only else "Video")
        self.flow.setVisible(job is not None)
        self.timeline_content.setVisible(job is not None)
        self.timeline_intro.setVisible(job is None)
        self.sidebar_layout.setStretch(1, int(job is not None))
        self.sidebar_layout.setStretch(2, int(job is None))
        if job is None:
            self.show_empty_preview("Mulai dari sumber media", "Pilih media di panel Siapkan Input.")
        elif self.active is not None:
            self.show_empty_preview("Sedang memproses", "Pantau progres di Tahapan Pemrosesan dan baca Log tahapan.", busy=True)
        elif job.status == "Selesai":
            self.show_empty_preview("Menyiapkan pratinjau", "Hasil akan tampil di sini setelah gambar dibuka.", busy=True)
        else:
            self.show_empty_preview("Input siap diproses", "Periksa pengaturan hasil, lalu klik Mulai.")
        self.timeline_details.clear()
        self.preview_generation += 1
        self.preview_pending = None
        self.preview.message = message
        self.preview.set_rotation(job.rotation if job else 0.0)
        self.preview.focus = job.focus if job else ()
        self.preview.show_images(())
        if self.preview.tone:
            self.toggle_tone()
        self.slider.setValue(50)
        self.tone_button.setEnabled(False)
        self.rotation_button.setEnabled(False)
        self.slider.setEnabled(False)
        self.records = []
        self.engine_records = []
        self.engine_offset = 0
        self.stage_index = self.active_stage = 0
        self.follow = True
        self.show_progress([])
        self.show_iteration()

    def update_controls(self):
        busy = self.active is not None
        job = self.jobs[self.current] if self.current is not None else None
        results = self.result_indices()
        for control in (self.video_button, self.frames_button, self.folder_button):
            control.setEnabled(not busy)
        has_orthomosaic = any(not job.enhance_only for job in self.jobs)
        self.accuracy_row.setVisible(has_orthomosaic)
        self.accuracy.setEnabled(not busy and has_orthomosaic)
        self.start_button.setEnabled(bool(self.jobs) and not busy)
        self.reset_button.setEnabled(bool(self.jobs) and not busy)
        self.pause_button.setEnabled(busy and self.command != "stop")
        self.stop_button.setEnabled(busy and self.command != "stop")
        if busy:
            hint = "Jeda dan Berhenti berlaku di batas tahap."
        elif not self.jobs:
            hint = "Pilih input untuk mengaktifkan Mulai."
        elif results:
            hint = "Reset untuk memulai sesi baru."
        else:
            hint = "Jeda dan Berhenti tersedia saat proses berjalan."
        self.control_hint.show_text(hint)
        pause_text = "Lanjut" if self.command == "pause" else "Jeda"
        if self.pause_button.source_text != pause_text:
            self.pause_button.setText(pause_text)
            self.pause_button.setIcon(self.resume_icon if pause_text == "Lanjut" else self.pause_icon)
        self.pause_button.setToolTip("Lanjutkan pemrosesan" if pause_text == "Lanjut" else "Jeda di batas tahap")
        self.queue_button.setText(f"Antrean ({len(self.jobs)})" if self.jobs else "Antrean")
        self.queue_button.setEnabled(bool(self.jobs))
        output_root = self.output_root()
        path = str(output_root) if output_root is not None else "Mengikuti folder file sumber"
        self.path_label.show_text(path)
        position = results.index(self.current) if not busy and self.current in results else None
        self.result_navigation.setVisible(position is not None)
        self.previous_result.setEnabled(position is not None and position > 0)
        self.next_result.setEnabled(position is not None and position + 1 < len(results))
        self.result_label.show_text(f"{position + 1}/{len(results)} · {job.title}" if position is not None else
                                    "Sedang memproses" if busy else "Belum ada hasil")
        for key, control in self.result_buttons.items():
            target = self.result_path(key)
            preparing = key == "enhanced" and self.output_export is not None
            control.setEnabled(not preparing and target is not None and target.exists())

    def start_batch(self):
        if not self.jobs or self.active is not None:
            return
        try:
            find_ffmpeg()
        except RuntimeError as exc:
            self.warn(str(exc))
            return
        self.jobs = [QueueEntry(job.source, frames=job.frames) for job in self.jobs]
        self.active = 0
        self.start_entry()

    def start_entry(self):
        if self.active is None or self.process is not None or self.command != "run":
            return
        job = self.jobs[self.active]
        self.configure_job(self.active, "Sedang memproses")
        job.status = "Berjalan"
        prefix = f"File {self.active + 1}/{len(self.jobs)} · " if len(self.jobs) > 1 else ""
        self.set_badge(f"{prefix}Memproses", "running")
        self.status.setText("Menyiapkan proses…")
        try:
            name = job.source.parent.name if job.frames else job.source.stem
            job.output = create_output(self.output_root(), name,
                                       "autotone" if job.enhance_only else "ortho")
            self.process = start_job(job.source, job.output, self.accuracy.currentText(), job.frames)
        except (RuntimeError, OSError) as exc:
            self.status.setText(f"Gagal memulai. {exc}")
            self.finish_entry("Gagal", str(exc))
            return
        self.update_controls()
        self.poll_timer.start()

    def poll_job(self):
        job = self.jobs[self.active]
        code = self.process.poll()
        durations, percent, marker, last_line = self.read_progress(job)
        running = code is None and marker.startswith("MULAI  ")
        self.show_progress(durations, percent, running)
        if code is None:
            if self.command == "pause":
                self.status.setText("Dijeda." if last_line == PAUSE_MARKER else "Menunggu jeda di batas tahap.")
                if last_line == PAUSE_MARKER and self.preview_mark.busy:
                    self.show_empty_preview("Proses dijeda", "Klik Lanjut untuk meneruskan pemrosesan.")
            elif self.command == "stop":
                self.status.setText("Menunggu berhenti di batas tahap.")
            elif running:
                self.status.setText(f"Tahap aktif · {self.stages[self.active_stage]}")
                total = self.total.target
                phase = "finishing" if total >= 66 else "working" if total >= 33 else "running"
                prefix = f"File {self.active + 1}/{len(self.jobs)} · " if len(self.jobs) > 1 else ""
                self.set_badge(f"{prefix}Memproses {total}%", phase)
            return
        self.process = None
        self.poll_timer.stop()
        if last_line == STOP_MARKER:
            self.finish_entry("Dihentikan")
            return
        png = self.result_path("enhanced")
        try:
            success = code == 0 and png.stat().st_size > 0
        except OSError:
            success = False
        self.finish_entry("Selesai" if success else "Gagal", "" if success else f"Kode keluar {code}. Buka log.")

    def finish_entry(self, status, error=""):
        job = self.jobs[self.active]
        job.status, job.error = status, error
        if self.command == "stop":
            for pending in self.jobs[self.active + 1:]:
                pending.status = "Dibatalkan"
        elif self.active + 1 < len(self.jobs):
            self.active += 1
            if self.command == "pause":
                self.show_paused_queue()
            else:
                QtCore.QTimer.singleShot(0, self.start_entry)
            return
        self.active = None
        stopped = self.command == "stop"
        self.command = "run"
        results = self.result_indices()
        if not results and not stopped:
            self.set_badge("Proses gagal", "error")
        else:
            self.set_badge(f"{'Dihentikan' if stopped else 'Selesai'} · {len(results)}/{len(self.jobs)} berhasil",
                           "done" if len(results) == len(self.jobs) else "warning")
        if results:
            self.show_result(results[-1])
        else:
            self.status.setText(error or ("Proses dihentikan." if stopped else "Pemrosesan gagal."))
            self.show_empty_preview("Proses dihentikan" if stopped else "Pemrosesan gagal", self.status.source_text)
        self.update_controls()

    def set_command(self, command):
        if self.active is None or self.command == "stop":
            return
        if self.process is not None:
            control = self.jobs[self.active].output / CONTROL_FILE
            temp = control.with_suffix(".tmp")
            try:
                temp.write_text(command, encoding="ascii")
                temp.replace(control)
            except OSError as exc:
                self.warn(str(exc))
                return
        self.command = command
        self.update_controls()
        if self.process is not None:
            if command == "run":
                self.show_empty_preview("Sedang memproses", "Pantau progres di Tahapan Pemrosesan dan baca Log tahapan.", busy=True)
            elif command == "stop":
                self.show_empty_preview("Menunggu berhenti", "Tahap aktif sedang diselesaikan.", busy=True)
        else:
            if command == "run":
                self.start_entry()
            elif command == "stop":
                self.finish_entry("Dibatalkan")
            else:
                self.show_paused_queue()

    def show_paused_queue(self):
        self.set_badge("Antrean dijeda", "warning")
        self.status.setText("Klik Lanjut untuk memulai file berikutnya.")
        self.show_empty_preview("Antrean dijeda", "Klik Lanjut untuk memulai file berikutnya.")

    def toggle_pause(self):
        self.set_command("run" if self.command == "pause" else "pause")

    def stop(self):
        if QtWidgets.QMessageBox.question(self, tr("Berhenti"), tr("Hentikan antrean setelah tahap aktif selesai?")) == QtWidgets.QMessageBox.Yes:
            self.set_command("stop")

    def read_progress(self, job):
        try:
            lines = (job.output / "proses.log").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        durations, percent, marker = timeline_status(lines)
        details = "  ·  ".join(line for line in lines if line.startswith(("Frame: ", "Kamera terselaras: ", "Total: ")))
        if self.timeline_details.text() != details:
            self.timeline_details.setText(details)
        self.records = []
        for line in lines:
            if line.startswith("MULAI  "):
                self.records.append([])
            if self.records and not line.startswith("PROGRES  "):
                self.records[-1].append(line)
        try:
            with (job.output / "metashape.log").open("rb") as engine:
                engine.seek(self.engine_offset)
                for raw in engine.readlines(256 * 1024):
                    if not raw.endswith(b"\n"):
                        break
                    self.engine_offset += len(raw)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line.startswith("ORTO TAHAP  "):
                        self.engine_records.append([])
                    elif self.engine_records and line:
                        self.engine_records[-1].append(line)
                        self.engine_records[-1] = self.engine_records[-1][-100:]
        except OSError:
            pass
        self.active_stage = min(len(self.stages) - 1, max(0, len(self.records) - 1))
        if self.follow:
            self.stage_index = self.active_stage
        self.show_iteration()
        return durations, percent, marker, lines[-1] if lines else ""

    def show_progress(self, durations, percent=None, running=False):
        complete = min(len(durations), len(self.stages))
        total = (100 * complete + (percent or 0)) // len(self.stages)
        self.total.show_value(total)
        self.total_text.setText(f"{complete}/{len(self.stages)} tahap · {total}%")
        states = []
        for index, (line, title, bar, detail) in enumerate(self.stage_rows):
            line.setVisible(index < len(self.stages))
            if index >= len(self.stages):
                continue
            title.show_text(f"{index + self.first_stage}. {self.stages[index]}")
            active = running and index == complete
            highlighted = active or index < complete
            states.append(highlighted)
            if detail.property("active") != highlighted:
                detail.setProperty("active", highlighted)
                detail.style().unpolish(detail)
                detail.style().polish(detail)
            value = 100 if index < complete else percent if active else 0
            bar.show_value(value)
            detail.setText(f"✓ {durations[index]}" if index < complete else "Berjalan" if value is None else f"{value}%")
        self.timeline_rail.show_states(tuple(states))

    def move_stage(self, direction):
        self.follow = False
        self.stage_index += direction
        self.show_iteration()

    def follow_stage(self):
        self.follow = True
        self.stage_index = self.active_stage
        self.show_iteration()

    def show_iteration(self):
        has_job = self.current is not None
        self.log_navigation.setVisible(has_job)
        self.log_stack.setCurrentIndex(int(has_job))
        if not has_job:
            self.stage_label.setText("Belum ada pekerjaan")
            return
        index = self.stage_index
        self.stage_label.setText(f"{index + self.first_stage}/{len(self.stages) + self.first_stage - 1} · {self.stages[index]}")
        self.previous_stage.setEnabled(index > 0)
        self.next_stage.setEnabled(index + 1 < len(self.stages))
        self.follow_button.setEnabled(not self.follow)
        records = self.records[index] if index < len(self.records) else [
            tr("Rincian muncul saat tahap ini berjalan." if self.active is not None else
               "Klik Mulai untuk melihat catatan pemrosesan.")
        ]
        engine = self.engine_records[index] if index < len(self.engine_records) else []
        content = "\n".join([*records, "", tr("KELUARAN MESIN"), *engine] if engine else records)
        if self.log.toPlainText() != content:
            scroll = self.log.verticalScrollBar()
            position = scroll.value()
            self.log.setPlainText(content)
            scroll.setValue(scroll.maximum() if self.follow else position)

    def result_indices(self):
        return [index for index, job in enumerate(self.jobs) if job.status == "Selesai"]

    def move_result(self, delta):
        results = self.result_indices()
        if self.current in results:
            index = results.index(self.current) + delta
            if 0 <= index < len(results):
                self.show_result(results[index])

    def show_result(self, index):
        if self.active is not None or index not in self.result_indices():
            return
        self.configure_job(index, "Menyiapkan pratinjau…")
        durations, _, _, _ = self.read_progress(self.jobs[index])
        self.show_progress(durations)
        self.status.setText("Selesai. PNG hasil tersimpan.")
        self.update_controls()
        self.preview_pending = (self.preview_generation, (self.result_path("original"), self.result_path("enhanced")))
        self.start_preview()

    def start_preview(self):
        if self.preview_worker is None and self.preview_pending is not None:
            self.preview_worker, paths = self.preview_pending
            self.preview_pending = None
            threading.Thread(target=render_previews, args=(paths, self.preview_requests), daemon=True).start()
            self.preview_timer.start()

    def poll_preview(self):
        try:
            rasters = self.preview_requests.get_nowait()
        except queue.Empty:
            return
        generation, self.preview_worker = self.preview_worker, None
        self.preview_timer.stop()
        if generation == self.preview_generation:
            if rasters:
                images = tuple(raster_pixmap(raster) for raster in rasters)
                self.preview.show_images(images)
                self.preview_hint.show_text("Roda mouse: zoom · Seret: geser")
                self.image_controls.show()
                self.compare_footer.show()
                self.preview_mark.set_busy(False)
                self.preview_stack.setCurrentWidget(self.preview)
                self.tone_button.setEnabled(True)
                self.rotation_button.setEnabled(True)
                self.slider.setEnabled(True)
            else:
                self.show_empty_preview("Pratinjau tidak tersedia", "Buka gambar melalui Berkas hasil.")
        self.start_preview()

    def result_path(self, key):
        job = self.jobs[self.current] if self.current is not None else None
        if job is None or job.output is None:
            return None
        if key == "folder":
            return job.output
        if key == "log":
            log = job.output / "proses.log"
            return log if log.is_file() else job.output / "metashape.log"
        if key == "original":
            return job.source if job.enhance_only else job.output / "orthomosaic_asli.tif"
        return job.output / "orthomosaic_enhanced.png"

    def open_result(self, key):
        path = self.result_path(key)
        if path is None:
            return
        if key == "enhanced":
            if self.output_export is not None:
                return
            job = self.jobs[self.current]
            if job.rotation or job.focus:
                self.output_export = path.with_name("orthomosaic_final.png")
                self.result_buttons[key].setText("Menyiapkan PNG…")
                self.update_controls()
                threading.Thread(target=export_final_png, daemon=True,
                                 args=(path, self.output_export, job.rotation, job.focus,
                                       self.output_export_results)).start()
                self.output_export_timer.start()
                return
        self.open_file(path)

    def poll_output_export(self):
        try:
            error = self.output_export_results.get_nowait()
        except queue.Empty:
            return
        self.output_export_timer.stop()
        path, self.output_export = self.output_export, None
        self.result_buttons["enhanced"].setText("PNG hasil")
        self.update_controls()
        if error is None:
            self.open_file(path)
        else:
            self.warn(error)

    def open_file(self, path):
        try:
            os.startfile(path)
        except OSError as exc:
            self.warn(str(exc))

    def show_queue(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle(tr("Antrean Pemrosesan"))
        dialog.resize(540, 290)
        layout = QtWidgets.QVBoxLayout(dialog)
        listing = QtWidgets.QListWidget()
        layout.addWidget(listing)
        view = button("Tampilkan hasil", lambda: dialog.accept())
        layout.addWidget(view)

        def refresh_queue():
            position = max(0, listing.currentRow())
            rows = [f"{tr(job.status)} · {tr(job.title)}" + (f" · {tr(job.error)}" if job.error else "") for job in self.jobs]
            if rows != [listing.item(index).text() for index in range(listing.count())]:
                listing.clear()
                listing.addItems(rows)
                listing.setCurrentRow(position)
            view.setEnabled(self.active is None and listing.currentRow() in self.result_indices())

        timer = QtCore.QTimer(dialog)
        timer.timeout.connect(refresh_queue)
        timer.start(300)
        listing.currentRowChanged.connect(lambda: view.setEnabled(
            self.active is None and listing.currentRow() in self.result_indices()))
        refresh_queue()
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self.show_result(listing.currentRow())
        timer.stop()

    def closeEvent(self, event):
        if self.active is not None:
            self.warn("Tunggu sampai proses berakhir atau hentikan antrean sebelum menutup.")
            event.ignore()
        elif self.output_export is not None:
            self.warn("Tunggu sampai PNG hasil selesai disiapkan.")
            event.ignore()
        else:
            event.accept()


def main():
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setOrganizationName(COMPANY_NAME)
    app.setStyleSheet(STYLE)
    window = Window()
    if len(sys.argv) > 1 and sys.argv[1]:
        window.set_sources((sys.argv[1],))
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
