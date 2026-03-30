"""
Comic Sub Tool - Scene Detection & Subtitle Merger
Yêu cầu: pip install PyQt5 opencv-python numpy python-docx
"""

import sys
import cv2
import numpy as np
import json
import tempfile
import subprocess
import zipfile
import urllib.request
import urllib.error
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QListWidget, QListWidgetItem, QTextEdit,
    QFileDialog, QProgressBar, QSplitter, QSpinBox, QDoubleSpinBox,
    QGroupBox, QMessageBox, QStatusBar, QSizePolicy, QCheckBox, QDialog,
    QDialogButtonBox, QRadioButton, QButtonGroup
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt5.QtGui import QPixmap, QImage, QColor, QIcon
import re
import os
from dataclasses import dataclass, field
from typing import List, Optional
from copy import deepcopy


APP_NAME = "Comic Sub Tool"
APP_VERSION = "0.1.12"
GITHUB_REPO = "Thnhphngz/ComicSubTool"
UPDATE_ASSET_NAME = "ComicSubTool-win.zip"
APP_EXE_NAME = "ComicSubTool.exe"
SOURCE_UPDATE_ASSET_NAME = "Comicsubtool.py"
APP_ICON_FILE = "app_icon.ico"


# ─────────────────────────────────────────────
# Data Classes
# ─────────────────────────────────────────────

@dataclass
class SubEntry:
    index: int
    start_ms: int
    end_ms: int
    text: str


@dataclass
class Scene:
    start_ms: int
    end_ms: int
    subs: List[SubEntry] = field(default_factory=list)
    thumbnail: Optional[np.ndarray] = None

    @property
    def merged_text(self):
        texts = []
        for s in self.subs:
            t = s.text.strip().replace('\n', ' ')
            t = re.sub(r'<[^>]+>', '', t)
            if t:
                texts.append(t)
        return ', '.join(texts)

    @property
    def duration_ms(self):
        return self.end_ms - self.start_ms


# ─────────────────────────────────────────────
# SRT Parser / Exporter
# ─────────────────────────────────────────────

def resource_path(relative_path: str) -> str:
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def load_app_icon() -> QIcon:
    icon_path = resource_path(APP_ICON_FILE)
    if os.path.exists(icon_path):
        return QIcon(icon_path)
    return QIcon()

def parse_srt_time(t: str) -> int:
    t = t.strip()
    h, m, rest = t.split(':')
    s, ms = rest.split(',')
    return int(h)*3600000 + int(m)*60000 + int(s)*1000 + int(ms)


def ms_to_srt_time(ms: int) -> str:
    ms = max(0, ms)
    h = ms // 3600000; ms %= 3600000
    m = ms // 60000;   ms %= 60000
    s = ms // 1000;    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def get_crop_rect(image: Optional[np.ndarray], zoom_pct: int,
                  offset_x_pct: int = 0, offset_y_pct: int = 0):
    if image is None:
        return None

    height, width = image.shape[:2]
    if zoom_pct <= 0:
        return 0, 0, width, height

    scale = 1.0 + (zoom_pct / 100.0)
    crop_w = max(1, int(round(width / scale)))
    crop_h = max(1, int(round(height / scale)))

    slack_x = max(0, width - crop_w)
    slack_y = max(0, height - crop_h)
    offset_x_pct = max(-100, min(100, offset_x_pct))
    offset_y_pct = max(-100, min(100, offset_y_pct))

    x1 = int(round((slack_x / 2) * (1 + offset_x_pct / 100.0)))
    y1 = int(round((slack_y / 2) * (1 + offset_y_pct / 100.0)))
    x1 = max(0, min(slack_x, x1))
    y1 = max(0, min(slack_y, y1))
    x2 = min(width, x1 + crop_w)
    y2 = min(height, y1 + crop_h)
    return x1, y1, x2, y2


def apply_zoom_crop(image: Optional[np.ndarray], zoom_pct: int,
                    offset_x_pct: int = 0, offset_y_pct: int = 0) -> Optional[np.ndarray]:
    if image is None:
        return image

    x1, y1, x2, y2 = get_crop_rect(image, zoom_pct, offset_x_pct, offset_y_pct)
    cropped = image[y1:y2, x1:x2]
    if cropped.size == 0:
        return image

    height, width = image.shape[:2]
    if x1 == 0 and y1 == 0 and x2 == width and y2 == height:
        return image
    return cv2.resize(cropped, (width, height), interpolation=cv2.INTER_CUBIC)


def create_crop_overlay(image: Optional[np.ndarray], zoom_pct: int,
                        offset_x_pct: int = 0, offset_y_pct: int = 0) -> Optional[np.ndarray]:
    if image is None:
        return None

    x1, y1, x2, y2 = get_crop_rect(image, zoom_pct, offset_x_pct, offset_y_pct)
    overlay = image.copy()
    shade = np.zeros_like(image)
    cv2.addWeighted(shade, 0.35, overlay, 0.65, 0, overlay)
    overlay[y1:y2, x1:x2] = image[y1:y2, x1:x2]
    cv2.rectangle(overlay, (x1, y1), (max(x1, x2 - 1), max(y1, y2 - 1)), (80, 220, 255), 3)
    return overlay


def cv_image_to_pixmap(image: Optional[np.ndarray], max_width: int, max_height: int) -> QPixmap:
    if image is None:
        return QPixmap()
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    h, w, c = rgb.shape
    qimg = QImage(rgb.data, w, h, w * c, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg).scaled(
        max_width, max_height, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )


def normalize_scene_fragment(text: str) -> str:
    text = text.strip().replace('\n', ' ')
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[.,;:!?]+$', '', text).strip()
    return text


def clean_export_line(text: str) -> str:
    text = text.strip().replace('\n', ' ')
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr)\.\s+', r'\1 ', text, flags=re.IGNORECASE)
    text = re.sub(r',\s*\.{3,}\s*', ', ', text)
    text = re.sub(r'\.{3,}\s*,', ', ', text)
    text = re.sub(r'\s*\.{3,}\s*', ', ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'\s*[.;:!?]+\s*,', ',', text)
    text = re.sub(r'\s*,\s*', ', ', text)
    text = re.sub(r'(,\s*){2,}', ', ', text)
    text = re.sub(r'\s+', ' ', text).strip(' ,')
    if not text:
        return ""

    fragments = []
    for fragment in text.split(','):
        cleaned = fragment.strip()
        cleaned = re.sub(r'[.;:!?]+$', '', cleaned).strip()
        if cleaned:
            fragments.append(cleaned)

    if not fragments:
        return ""
    return ', '.join(fragments) + '.'


def parse_version(version: str):
    parts = []
    for piece in re.findall(r'\d+', version or ""):
        parts.append(int(piece))
    return tuple(parts or [0])


def is_newer_version(latest: str, current: str) -> bool:
    latest_parts = parse_version(latest)
    current_parts = parse_version(current)
    size = max(len(latest_parts), len(current_parts))
    latest_parts += (0,) * (size - len(latest_parts))
    current_parts += (0,) * (size - len(current_parts))
    return latest_parts > current_parts


def format_scene_line(subs: List[SubEntry]) -> str:
    fragments = []
    prev = None
    for sub in subs:
        text = normalize_scene_fragment(sub.text)
        if not text or text == prev:
            continue
        fragments.append(text)
        prev = text

    if not fragments:
        return ""
    return ', '.join(fragments) + '.'


def parse_srt(path: str) -> List[SubEntry]:
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        content = f.read()
    entries = []
    blocks = re.split(r'\n\s*\n', content.strip())
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        try:
            idx = int(lines[0].strip())
            times = lines[1].split('-->')
            start = parse_srt_time(times[0])
            end   = parse_srt_time(times[1])
            text  = '\n'.join(lines[2:]).strip() if len(lines) > 2 else ''
            entries.append(SubEntry(idx, start, end, text))
        except Exception:
            pass
    return entries


def export_srt(scenes: List[Scene], path: str):
    lines = []
    idx = 1
    for scene in scenes:
        text = scene.merged_text
        if not text:
            continue
        lines.append(str(idx))
        lines.append(f"{ms_to_srt_time(scene.start_ms)} --> {ms_to_srt_time(scene.end_ms)}")
        lines.append(text)
        lines.append('')
        idx += 1
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


# ─────────────────────────────────────────────
# Scene Detection Worker Thread
# ─────────────────────────────────────────────

class DetectWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, video_path, subs, threshold, min_scene_ms,
                 sub_crop_pct, stabilize_frames):
        super().__init__()
        self.video_path       = video_path
        self.subs             = subs
        self.threshold        = threshold
        self.min_scene_ms     = min_scene_ms
        self.sub_crop_pct     = sub_crop_pct
        self.stabilize_frames = stabilize_frames

    def run(self):
        try:
            self.finished.emit(self._detect())
        except Exception as e:
            self.error.emit(str(e))

    def _detect(self) -> List[Scene]:
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise RuntimeError("Không mở được video")

        fps      = cap.get(cv2.CAP_PROP_FPS) or 30
        total_fr = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        height   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        crop_h   = int(height * (1 - self.sub_crop_pct))

        # Pass 1: diff
        self.progress.emit(0, "Đang phân tích video...")
        diffs = []
        prev_gray = None
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            roi  = frame[:crop_h, :]
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (320, 180))
            if prev_gray is not None:
                diff = np.mean(np.abs(gray.astype(float) - prev_gray.astype(float)))
                diffs.append(diff)
            else:
                diffs.append(0.0)
            prev_gray = gray
            frame_idx += 1
            if frame_idx % 100 == 0:
                pct = int(frame_idx / total_fr * 50)
                self.progress.emit(pct, f"Pass 1: {frame_idx}/{total_fr} frames")

        # Pass 2: spike detection
        self.progress.emit(50, "Phát hiện chuyển cảnh...")
        diffs = np.array(diffs)
        win = max(1, int(fps * 1.5))

        def rolling_mean(arr, w):
            result = np.zeros_like(arr)
            for i in range(len(arr)):
                result[i] = arr[max(0, i-w):i+1].mean()
            return result

        baseline = rolling_mean(diffs, win)
        is_spike = (diffs > baseline * self.threshold) & (diffs > 3.0)

        transitions = []
        in_trans = False
        trans_start = 0
        for i, sp in enumerate(is_spike):
            if sp and not in_trans:
                in_trans = True; trans_start = i
            elif not sp and in_trans:
                transitions.append((trans_start, i)); in_trans = False
        if in_trans:
            transitions.append((trans_start, len(is_spike)))

        scene_start_frames = [0]
        for (ts, te) in transitions:
            start = te + self.stabilize_frames
            if start < total_fr:
                scene_start_frames.append(start)

        min_frames = int(self.min_scene_ms / 1000 * fps)
        filtered = [scene_start_frames[0]]
        for f in scene_start_frames[1:]:
            if f - filtered[-1] >= min_frames:
                filtered.append(f)
        scene_start_frames = filtered

        # Pass 3: thumbnails
        self.progress.emit(75, "Lấy thumbnail...")
        scene_list: List[Scene] = []
        n = len(scene_start_frames)
        for i, sf in enumerate(scene_start_frames):
            ef = scene_start_frames[i+1] if i+1 < n else total_fr
            start_ms = int(sf / fps * 1000)
            end_ms   = int(ef / fps * 1000)
            mid_frame = (sf + ef) // 2
            cap.set(cv2.CAP_PROP_POS_FRAMES, mid_frame)
            ret, thumb = cap.read()
            scene_list.append(Scene(start_ms=start_ms, end_ms=end_ms,
                                    thumbnail=thumb if ret else None))
            if i % 10 == 0:
                self.progress.emit(75 + int(i / n * 20), f"Thumbnail {i+1}/{n}")

        cap.release()

        # Map sub vào cảnh
        self.progress.emit(95, "Ghép subtitle...")
        for sub in self.subs:
            mid = (sub.start_ms + sub.end_ms) // 2
            best = None; best_dist = float('inf')
            for sc in scene_list:
                if sc.start_ms <= mid < sc.end_ms:
                    best = sc; break
                dist = min(abs(mid - sc.start_ms), abs(mid - sc.end_ms))
                if dist < best_dist:
                    best_dist = dist; best = sc
            if best is not None:
                best.subs.append(sub)

        self.progress.emit(100, "Xong!")
        return scene_list


# ─────────────────────────────────────────────
# Save Images Worker Thread
# ─────────────────────────────────────────────

class SaveImagesWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(int)   # số ảnh đã lưu
    error    = pyqtSignal(str)

    def __init__(self, scenes: List[Scene], output_dir: str,
                 img_format: str, only_with_sub: bool, zoom_pct: int,
                 offset_x_pct: int, offset_y_pct: int):
        super().__init__()
        self.scenes       = scenes
        self.output_dir   = output_dir
        self.img_format   = img_format       # "jpg" hoặc "png"
        self.only_with_sub = only_with_sub
        self.zoom_pct     = max(0, zoom_pct)
        self.offset_x_pct = max(-100, min(100, offset_x_pct))
        self.offset_y_pct = max(-100, min(100, offset_y_pct))

    def run(self):
        try:
            saved = 0
            scenes_to_save = [
                (i, sc) for i, sc in enumerate(self.scenes)
                if sc.thumbnail is not None
                and (not self.only_with_sub or sc.subs)
            ]
            total = len(scenes_to_save)
            for idx, (i, sc) in enumerate(scenes_to_save):
                filename = f"scene_{i+1:04d}.{self.img_format}"
                filepath = os.path.join(self.output_dir, filename)
                output_image = apply_zoom_crop(
                    sc.thumbnail, self.zoom_pct, self.offset_x_pct, self.offset_y_pct
                )
                cv2.imwrite(filepath, output_image)
                saved += 1
                pct = int((idx + 1) / total * 100)
                self.progress.emit(pct, f"Đang lưu {filename} ({idx+1}/{total})")
            self.finished.emit(saved)
        except Exception as e:
            self.error.emit(str(e))


# ─────────────────────────────────────────────
# Update Worker Thread
# ─────────────────────────────────────────────

class UpdateCheckWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, repo_full_name: str, preferred_asset_name: str):
        super().__init__()
        self.repo_full_name = repo_full_name.strip().strip("/")
        self.preferred_asset_name = preferred_asset_name.strip()

    def run(self):
        try:
            api_url = f"https://api.github.com/repos/{self.repo_full_name}/releases/latest"
            req = urllib.request.Request(
                api_url,
                headers={
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                    "Accept": "application/vnd.github+json"
                }
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            assets = data.get("assets") or []
            chosen_asset = None
            if self.preferred_asset_name:
                for asset in assets:
                    if asset.get("name", "").lower() == self.preferred_asset_name.lower():
                        chosen_asset = asset
                        break

            if chosen_asset is None:
                for asset in assets:
                    if asset.get("name", "").lower().endswith(".exe"):
                        chosen_asset = asset
                        break

            if chosen_asset is None and assets:
                chosen_asset = assets[0]

            release_info = {
                "version": str(data.get("tag_name") or data.get("name") or "").strip(),
                "notes": str(data.get("body") or "").strip(),
                "asset_name": str((chosen_asset or {}).get("name", "")).strip(),
                "asset_url": str((chosen_asset or {}).get("browser_download_url", "")).strip(),
                "html_url": str(data.get("html_url") or "").strip(),
            }
            self.finished.emit(release_info)
        except Exception as e:
            self.error.emit(str(e))


# ─────────────────────────────────────────────
# Save Images Dialog
# ─────────────────────────────────────────────

class SaveImagesDialog(QDialog):
    def __init__(self, sample_image=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Lưu hình ảnh")
        self.setMinimumWidth(760)
        self.sample_image = sample_image
        layout = QVBoxLayout(self)

        # Format
        fmt_group = QGroupBox("Định dạng ảnh")
        fmt_layout = QHBoxLayout(fmt_group)
        self.radio_jpg = QRadioButton("JPG (nhỏ hơn)")
        self.radio_png = QRadioButton("PNG (chất lượng cao)")
        self.radio_png.setChecked(True)
        fmt_layout.addWidget(self.radio_jpg)
        fmt_layout.addWidget(self.radio_png)
        layout.addWidget(fmt_group)

        # Filter
        filter_group = QGroupBox("Lọc cảnh")
        filter_layout = QVBoxLayout(filter_group)
        self.chk_only_sub = QCheckBox("Chỉ lưu cảnh có subtitle")
        self.chk_only_sub.setChecked(False)
        filter_layout.addWidget(self.chk_only_sub)
        layout.addWidget(filter_group)

        # Zoom crop
        zoom_group = QGroupBox("Cắt / zoom ảnh")
        zoom_layout = QHBoxLayout(zoom_group)
        zoom_layout.addWidget(QLabel("Zoom vào (%):"))
        self.spin_zoom_pct = QSpinBox()
        self.spin_zoom_pct.setRange(0, 50)
        self.spin_zoom_pct.setValue(10)
        self.spin_zoom_pct.setSingleStep(1)
        self.spin_zoom_pct.setSuffix("%")
        self.spin_zoom_pct.setToolTip("0% = giữ nguyên. Tăng % để crop rồi phóng lại.")
        self.spin_zoom_pct.valueChanged.connect(self.update_preview)
        zoom_layout.addWidget(self.spin_zoom_pct)
        zoom_layout.addStretch()
        layout.addWidget(zoom_group)

        offset_group = QGroupBox("Vị trí crop")
        offset_layout = QVBoxLayout(offset_group)

        offset_x_row = QHBoxLayout()
        offset_x_row.addWidget(QLabel("Lệch ngang:"))
        self.spin_offset_x = QSpinBox()
        self.spin_offset_x.setRange(-100, 100)
        self.spin_offset_x.setValue(0)
        self.spin_offset_x.setSingleStep(5)
        self.spin_offset_x.setSuffix("%")
        self.spin_offset_x.setToolTip("-100% = hết cỡ sang trái, 100% = hết cỡ sang phải.")
        self.spin_offset_x.valueChanged.connect(self.update_preview)
        offset_x_row.addWidget(self.spin_offset_x)
        offset_x_row.addWidget(QLabel("Trái"))
        offset_x_row.addStretch()
        offset_x_row.addWidget(QLabel("Phải"))
        offset_layout.addLayout(offset_x_row)

        offset_y_row = QHBoxLayout()
        offset_y_row.addWidget(QLabel("Lệch dọc:"))
        self.spin_offset_y = QSpinBox()
        self.spin_offset_y.setRange(-100, 100)
        self.spin_offset_y.setValue(0)
        self.spin_offset_y.setSingleStep(5)
        self.spin_offset_y.setSuffix("%")
        self.spin_offset_y.setToolTip("-100% = hết cỡ lên trên, 100% = hết cỡ xuống dưới.")
        self.spin_offset_y.valueChanged.connect(self.update_preview)
        offset_y_row.addWidget(self.spin_offset_y)
        offset_y_row.addWidget(QLabel("Trên"))
        offset_y_row.addStretch()
        offset_y_row.addWidget(QLabel("Dưới"))
        offset_layout.addLayout(offset_y_row)

        layout.addWidget(offset_group)

        # Preview
        preview_group = QGroupBox("Ảnh mẫu")
        preview_layout = QHBoxLayout(preview_group)

        original_layout = QVBoxLayout()
        original_label = QLabel("Gốc")
        original_label.setStyleSheet("font-weight: bold;")
        self.lbl_preview_original = QLabel("Không có ảnh mẫu")
        self.lbl_preview_original.setAlignment(Qt.AlignCenter)
        self.lbl_preview_original.setMinimumSize(320, 180)
        self.lbl_preview_original.setStyleSheet(
            "background: #1a1a2e; border: 1px solid #444; border-radius: 4px;")
        original_layout.addWidget(original_label)
        original_layout.addWidget(self.lbl_preview_original)
        preview_layout.addLayout(original_layout)

        zoomed_layout = QVBoxLayout()
        self.lbl_preview_title = QLabel("Zoom")
        self.lbl_preview_title.setStyleSheet("font-weight: bold;")
        self.lbl_preview_zoomed = QLabel("Không có ảnh mẫu")
        self.lbl_preview_zoomed.setAlignment(Qt.AlignCenter)
        self.lbl_preview_zoomed.setMinimumSize(320, 180)
        self.lbl_preview_zoomed.setStyleSheet(
            "background: #1a1a2e; border: 1px solid #444; border-radius: 4px;")
        zoomed_layout.addWidget(self.lbl_preview_title)
        zoomed_layout.addWidget(self.lbl_preview_zoomed)
        preview_layout.addLayout(zoomed_layout)

        layout.addWidget(preview_group)

        # Output dir
        dir_layout = QHBoxLayout()
        self.lbl_dir = QLabel("Chưa chọn thư mục")
        self.lbl_dir.setStyleSheet("color: #aaa; font-size: 11px;")
        self.lbl_dir.setWordWrap(True)
        btn_choose = QPushButton("Chọn thư mục...")
        btn_choose.setFixedWidth(130)
        btn_choose.clicked.connect(self.choose_dir)
        dir_layout.addWidget(self.lbl_dir, 1)
        dir_layout.addWidget(btn_choose)
        layout.addLayout(dir_layout)

        self.output_dir = None

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.Ok).setText("Lưu")
        btns.button(QDialogButtonBox.Cancel).setText("Hủy")
        layout.addWidget(btns)
        self.update_preview()

    def choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu ảnh")
        if d:
            self.output_dir = d
            self.lbl_dir.setText(d)

    def update_preview(self):
        zoom_pct = self.spin_zoom_pct.value()
        offset_x_pct = self.spin_offset_x.value()
        offset_y_pct = self.spin_offset_y.value()
        self.lbl_preview_title.setText(
            f"Zoom {zoom_pct}% | X {offset_x_pct:+d}% | Y {offset_y_pct:+d}%"
        )
        if self.sample_image is None:
            self.lbl_preview_original.setText("Không có ảnh mẫu")
            self.lbl_preview_original.setPixmap(QPixmap())
            self.lbl_preview_zoomed.setText("Không có ảnh mẫu")
            self.lbl_preview_zoomed.setPixmap(QPixmap())
            return

        original_pixmap = cv_image_to_pixmap(
            create_crop_overlay(self.sample_image, zoom_pct, offset_x_pct, offset_y_pct),
            320, 180
        )
        zoomed_pixmap = cv_image_to_pixmap(
            apply_zoom_crop(self.sample_image, zoom_pct, offset_x_pct, offset_y_pct),
            320, 180
        )
        self.lbl_preview_original.setText("")
        self.lbl_preview_zoomed.setText("")
        self.lbl_preview_original.setPixmap(original_pixmap)
        self.lbl_preview_zoomed.setPixmap(zoomed_pixmap)

    @property
    def img_format(self):
        return "png" if self.radio_png.isChecked() else "jpg"

    @property
    def only_with_sub(self):
        return self.chk_only_sub.isChecked()

    @property
    def zoom_pct(self):
        return self.spin_zoom_pct.value()

    @property
    def offset_x_pct(self):
        return self.spin_offset_x.value()

    @property
    def offset_y_pct(self):
        return self.spin_offset_y.value()


# ─────────────────────────────────────────────
# Main Window
# ─────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setWindowIcon(load_app_icon())
        self.setMinimumSize(1100, 700)
        self.video_path  = None
        self.srt_path    = None
        self.subs        = []
        self.scenes: List[Scene] = []
        self.current_idx = -1
        self.worker      = None
        self.save_worker = None
        self.update_worker = None
        self._build_ui()
        self._apply_dark_theme()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(6)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # ── Toolbar row 1 ──
        toolbar = QHBoxLayout()

        self.btn_video     = QPushButton("📁 Mở Video.")
        self.btn_srt       = QPushButton("📄 Mở SRT")
        self.btn_detect    = QPushButton("🔍 Detect Cảnh")
        self.btn_export    = QPushButton("💾 Export SRT")
        self.btn_clean_srt = QPushButton("🧹 Clean SRT → Word")
        self.btn_save_imgs = QPushButton("🖼 Lưu Hình Ảnh")
        self.btn_update    = QPushButton("⬇ Cập nhật")

        self.btn_detect.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_save_imgs.setEnabled(False)

        self.btn_video.clicked.connect(self.open_video)
        self.btn_srt.clicked.connect(self.open_srt)
        self.btn_detect.clicked.connect(self.run_detection)
        self.btn_export.clicked.connect(self.export_srt)
        self.btn_clean_srt.clicked.connect(self.clean_srt_to_docx)
        self.btn_save_imgs.clicked.connect(self.save_images)
        self.btn_update.clicked.connect(self.check_for_updates)

        for btn in [self.btn_video, self.btn_srt, self.btn_detect,
                    self.btn_export, self.btn_clean_srt, self.btn_save_imgs,
                    self.btn_update]:
            btn.setFixedHeight(36)
            toolbar.addWidget(btn)

        toolbar.addStretch()

        self.lbl_video_name = QLabel("Chưa chọn video")
        self.lbl_srt_name   = QLabel("Chưa chọn SRT")
        self.lbl_video_name.setStyleSheet("color: #aaa; font-size: 11px;")
        self.lbl_srt_name.setStyleSheet("color: #aaa; font-size: 11px;")
        info_layout = QVBoxLayout()
        info_layout.addWidget(self.lbl_video_name)
        info_layout.addWidget(self.lbl_srt_name)
        toolbar.addLayout(info_layout)
        main_layout.addLayout(toolbar)

        # ── Settings ──
        settings_box = QGroupBox("Cài đặt Detection")
        settings_layout = QHBoxLayout(settings_box)
        settings_layout.setSpacing(12)

        settings_layout.addWidget(QLabel("Độ nhạy spike:"))
        self.spin_threshold = QDoubleSpinBox()
        self.spin_threshold.setRange(1.5, 10.0)
        self.spin_threshold.setValue(3.0)
        self.spin_threshold.setSingleStep(0.5)
        self.spin_threshold.setToolTip("Càng thấp = bắt nhiều hơn. Mặc định: 3.0")
        settings_layout.addWidget(self.spin_threshold)

        settings_layout.addWidget(QLabel("Cảnh tối thiểu (ms):"))
        self.spin_min_scene = QSpinBox()
        self.spin_min_scene.setRange(200, 5000)
        self.spin_min_scene.setValue(800)
        self.spin_min_scene.setSingleStep(100)
        settings_layout.addWidget(self.spin_min_scene)

        settings_layout.addWidget(QLabel("Crop sub bottom (%):"))
        self.spin_crop = QDoubleSpinBox()
        self.spin_crop.setRange(0, 0.4)
        self.spin_crop.setValue(0.12)
        self.spin_crop.setSingleStep(0.02)
        settings_layout.addWidget(self.spin_crop)

        settings_layout.addWidget(QLabel("Stabilize frames:"))
        self.spin_stabilize = QSpinBox()
        self.spin_stabilize.setRange(1, 30)
        self.spin_stabilize.setValue(5)
        settings_layout.addWidget(self.spin_stabilize)

        settings_layout.addStretch()
        main_layout.addWidget(settings_box)

        # ── Progress ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(18)
        main_layout.addWidget(self.progress_bar)

        # ── Splitter ──
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter, 1)

        # Left
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        lbl_scenes = QLabel("Danh sách cảnh")
        lbl_scenes.setStyleSheet("font-weight: bold; padding: 4px;")
        left_layout.addWidget(lbl_scenes)
        self.lbl_scene_count = QLabel("")
        self.lbl_scene_count.setStyleSheet("color: #aaa; font-size: 11px; padding: 2px 4px;")
        left_layout.addWidget(self.lbl_scene_count)
        self.scene_list = QListWidget()
        self.scene_list.setIconSize(QSize(120, 68))
        self.scene_list.currentRowChanged.connect(self.on_scene_selected)
        left_layout.addWidget(self.scene_list)
        splitter.addWidget(left_widget)

        # Right
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(8, 0, 0, 0)

        self.lbl_thumbnail = QLabel("Chọn một cảnh để xem")
        self.lbl_thumbnail.setAlignment(Qt.AlignCenter)
        self.lbl_thumbnail.setMinimumHeight(300)
        self.lbl_thumbnail.setStyleSheet(
            "background: #1a1a2e; border: 1px solid #444; border-radius: 4px;")
        right_layout.addWidget(self.lbl_thumbnail, 2)

        self.lbl_time = QLabel("")
        self.lbl_time.setStyleSheet("color: #88aaff; font-size: 12px; padding: 4px;")
        right_layout.addWidget(self.lbl_time)

        sub_label = QLabel("Subtitle (có thể chỉnh sửa):")
        sub_label.setStyleSheet("font-weight: bold; padding: 2px;")
        right_layout.addWidget(sub_label)

        self.txt_sub = QTextEdit()
        self.txt_sub.setMaximumHeight(100)
        self.txt_sub.setPlaceholderText("Không có subtitle cho cảnh này")
        right_layout.addWidget(self.txt_sub)

        btn_row = QHBoxLayout()
        self.btn_merge_prev = QPushButton("⬆ Gộp với cảnh trước")
        self.btn_merge_next = QPushButton("⬇ Gộp với cảnh sau")
        self.btn_split      = QPushButton("✂ Tách cảnh này")
        self.btn_no_sub     = QPushButton("🚫 Không có sub")
        self.btn_merge_prev.clicked.connect(self.merge_with_prev)
        self.btn_merge_next.clicked.connect(self.merge_with_next)
        self.btn_split.clicked.connect(self.split_scene)
        self.btn_no_sub.clicked.connect(self.clear_sub)
        for btn in [self.btn_merge_prev, self.btn_merge_next,
                    self.btn_split, self.btn_no_sub]:
            btn.setEnabled(False)
            btn_row.addWidget(btn)
        right_layout.addLayout(btn_row)

        sub_detail_label = QLabel("Các dòng sub trong cảnh này:")
        sub_detail_label.setStyleSheet("font-weight: bold; padding: 2px;")
        right_layout.addWidget(sub_detail_label)
        self.txt_sub_detail = QTextEdit()
        self.txt_sub_detail.setReadOnly(True)
        self.txt_sub_detail.setMaximumHeight(120)
        self.txt_sub_detail.setStyleSheet("font-size: 11px; color: #aaa;")
        right_layout.addWidget(self.txt_sub_detail)

        splitter.addWidget(right_widget)
        splitter.setSizes([300, 700])

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage(f"San sang. {APP_NAME} v{APP_VERSION}.")

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #0f0f1a; color: #e0e0e0; }
            QPushButton {
                background-color: #1e3a5f; color: white;
                border: 1px solid #2a5298; border-radius: 4px;
                padding: 4px 12px; font-size: 12px;
            }
            QPushButton:hover { background-color: #2a5298; }
            QPushButton:disabled { background-color: #333; color: #666; border-color: #444; }
            QListWidget { background-color: #111120; border: 1px solid #333; border-radius: 4px; }
            QListWidget::item { padding: 4px; border-bottom: 1px solid #222; }
            QListWidget::item:selected { background-color: #1e3a5f; }
            QListWidget::item:hover { background-color: #1a2a40; }
            QTextEdit { background-color: #111120; color: #e0e0e0;
                        border: 1px solid #333; border-radius: 4px; padding: 4px; }
            QGroupBox { border: 1px solid #333; border-radius: 4px;
                        margin-top: 8px; padding-top: 4px;
                        font-weight: bold; color: #aaa; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; }
            QProgressBar { border: 1px solid #333; border-radius: 3px;
                           background: #111; text-align: center; }
            QProgressBar::chunk { background-color: #2a5298; }
            QSpinBox, QDoubleSpinBox { background-color: #111120; color: #e0e0e0;
                                       border: 1px solid #333; border-radius: 3px; padding: 2px; }
            QSplitter::handle { background-color: #333; }
            QScrollBar:vertical { background: #111; width: 10px; }
            QScrollBar::handle:vertical { background: #444; border-radius: 4px; }
            QDialog { background-color: #0f0f1a; color: #e0e0e0; }
            QRadioButton, QCheckBox { color: #e0e0e0; }
        """)

    # ── File Loading ──────────────────────────────────────────────

    def open_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Chon video", "",
            "Video (*.mp4 *.mkv *.avi *.mov *.webm);;Tat ca (*)")
        if path:
            self.video_path = path
            self.lbl_video_name.setText(f"Video: {os.path.basename(path)}")
            self._check_ready()
            self.status.showMessage(f"Da tai video: {os.path.basename(path)}")

    def open_srt(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Chon file SRT", "", "SRT (*.srt);;Tat ca (*)")
        if path:
            self.srt_path = path
            self.subs = parse_srt(path)
            self.lbl_srt_name.setText(
                f"SRT: {os.path.basename(path)} ({len(self.subs)} dong)")
            self._check_ready()
            self.status.showMessage(f"Da tai SRT: {len(self.subs)} dong subtitle")

    def _check_ready(self):
        self.btn_detect.setEnabled(bool(self.video_path and self.srt_path))

    # ── Detection ─────────────────────────────────────────────────

    def run_detection(self):
        self.btn_detect.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_save_imgs.setEnabled(False)
        self.scene_list.clear()
        self.scenes = []
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.worker = DetectWorker(
            video_path       = self.video_path,
            subs             = self.subs,
            threshold        = self.spin_threshold.value(),
            min_scene_ms     = self.spin_min_scene.value(),
            sub_crop_pct     = self.spin_crop.value(),
            stabilize_frames = self.spin_stabilize.value()
        )
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_detect_done)
        self.worker.error.connect(self.on_detect_error)
        self.worker.start()

    def on_progress(self, pct, msg):
        self.progress_bar.setValue(pct)
        self.status.showMessage(msg)

    def on_detect_done(self, scenes):
        self.scenes = scenes
        self.progress_bar.setVisible(False)
        self.btn_detect.setEnabled(True)
        self.btn_export.setEnabled(True)
        self.btn_save_imgs.setEnabled(True)
        self._populate_scene_list()
        self.status.showMessage(
            f"Phat hien {len(scenes)} canh. "
            f"Canh co sub: {sum(1 for s in scenes if s.subs)}")

    def on_detect_error(self, msg):
        self.progress_bar.setVisible(False)
        self.btn_detect.setEnabled(True)
        QMessageBox.critical(self, "Loi", f"Loi khi phan tich video:\n{msg}")

    # ── Scene List ────────────────────────────────────────────────

    def _populate_scene_list(self):
        self.scene_list.clear()
        for i, scene in enumerate(self.scenes):
            item = QListWidgetItem()
            has_sub = bool(scene.subs)
            if scene.thumbnail is not None:
                thumb = cv2.resize(scene.thumbnail, (120, 68))
                rgb   = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
                h, w, c = rgb.shape
                qimg  = QImage(rgb.data, w, h, w * c, QImage.Format_RGB888)
                item.setIcon(QIcon(QPixmap.fromImage(qimg)))
            duration    = scene.duration_ms / 1000
            sub_preview = scene.merged_text[:50] + ('...' if len(scene.merged_text) > 50 else '')
            label = f"[{i+1}] {ms_to_srt_time(scene.start_ms)[:8]}  ({duration:.1f}s)\n"
            label += sub_preview if has_sub else "(khong co sub)"
            item.setText(label)
            if not has_sub:
                item.setForeground(QColor('#666'))
            self.scene_list.addItem(item)
        self.lbl_scene_count.setText(f"{len(self.scenes)} canh")

    def _update_scene_list_item(self, idx):
        if idx < 0 or idx >= self.scene_list.count():
            return
        item  = self.scene_list.item(idx)
        scene = self.scenes[idx]
        has_sub  = bool(scene.subs)
        duration = scene.duration_ms / 1000
        sub_preview = scene.merged_text[:50] + ('...' if len(scene.merged_text) > 50 else '')
        label = f"[{idx+1}] {ms_to_srt_time(scene.start_ms)[:8]}  ({duration:.1f}s)\n"
        label += sub_preview if has_sub else "(khong co sub)"
        item.setText(label)
        item.setForeground(QColor('#e0e0e0') if has_sub else QColor('#666'))

    # ── Scene Selection ───────────────────────────────────────────

    def on_scene_selected(self, idx):
        self.current_idx = idx
        if idx < 0 or idx >= len(self.scenes):
            return
        scene = self.scenes[idx]

        if scene.thumbnail is not None:
            rgb = cv2.cvtColor(scene.thumbnail, cv2.COLOR_BGR2RGB)
            h, w, c = rgb.shape
            qimg = QImage(rgb.data, w, h, w * c, QImage.Format_RGB888)
            pix  = QPixmap.fromImage(qimg).scaled(
                self.lbl_thumbnail.width() - 4,
                self.lbl_thumbnail.height() - 4,
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.lbl_thumbnail.setPixmap(pix)
        else:
            self.lbl_thumbnail.setText("Khong co thumbnail")

        self.lbl_time.setText(
            f"  {ms_to_srt_time(scene.start_ms)}  ->  {ms_to_srt_time(scene.end_ms)}"
            f"   ({scene.duration_ms/1000:.2f}s)")

        self.txt_sub.blockSignals(True)
        self.txt_sub.setText(scene.merged_text)
        self.txt_sub.blockSignals(False)

        if scene.subs:
            self.txt_sub_detail.setText('\n'.join(
                f"[{ms_to_srt_time(s.start_ms)[:8]} -> {ms_to_srt_time(s.end_ms)[:8]}] {s.text.strip()}"
                for s in scene.subs))
        else:
            self.txt_sub_detail.setText("(khong co sub nao)")

        for btn in [self.btn_merge_prev, self.btn_merge_next,
                    self.btn_split, self.btn_no_sub]:
            btn.setEnabled(True)
        self.btn_merge_prev.setEnabled(idx > 0)
        self.btn_merge_next.setEnabled(idx < len(self.scenes) - 1)

    # ── Scene Editing ─────────────────────────────────────────────

    def merge_with_prev(self):
        i = self.current_idx
        if i <= 0: return
        prev, curr = self.scenes[i-1], self.scenes[i]
        prev.end_ms = curr.end_ms
        prev.subs   = prev.subs + curr.subs
        if prev.thumbnail is None: prev.thumbnail = curr.thumbnail
        self.scenes.pop(i)
        self._populate_scene_list()
        self.scene_list.setCurrentRow(max(0, i-1))

    def merge_with_next(self):
        i = self.current_idx
        if i >= len(self.scenes) - 1: return
        curr, nxt = self.scenes[i], self.scenes[i+1]
        curr.end_ms = nxt.end_ms
        curr.subs   = curr.subs + nxt.subs
        self.scenes.pop(i+1)
        self._populate_scene_list()
        self.scene_list.setCurrentRow(i)

    def split_scene(self):
        i = self.current_idx
        scene = self.scenes[i]
        if not scene.subs:
            QMessageBox.information(self, "Thong bao", "Canh nay khong co sub de tach."); return
        if len(scene.subs) < 2:
            QMessageBox.information(self, "Thong bao", "Can it nhat 2 dong sub de tach canh."); return
        mid      = len(scene.subs) // 2
        split_ms = scene.subs[mid].start_ms
        self.scenes[i] = Scene(start_ms=scene.start_ms, end_ms=split_ms,
                               subs=scene.subs[:mid], thumbnail=scene.thumbnail)
        self.scenes.insert(i+1, Scene(start_ms=split_ms, end_ms=scene.end_ms,
                                      subs=scene.subs[mid:], thumbnail=scene.thumbnail))
        self._populate_scene_list()
        self.scene_list.setCurrentRow(i)

    def clear_sub(self):
        i = self.current_idx
        if i < 0: return
        self.scenes[i].subs = []
        self.txt_sub.blockSignals(True)
        self.txt_sub.clear()
        self.txt_sub.blockSignals(False)
        self._update_scene_list_item(i)

    # ── Clean SRT → Word (.docx) ──────────────────────────────────

    def clean_srt_to_docx(self):
        """Clean text theo từng cảnh hoặc từng block SRT, không giữ tiêu đề/timestamp."""
        try:
            from docx import Document as DocxDocument
            from docx.shared import Pt
        except ImportError:
            QMessageBox.critical(
                self, "Thieu thu vien",
                "Can cai dat python-docx:\n\npip install python-docx")
            return

        if self.current_idx >= 0:
            self._sync_edited_text(self.current_idx)

        lines = []
        export_mode = "scene"
        title_source_path = self.srt_path

        if self.scenes:
            lines = [clean_export_line(scene.merged_text) for scene in self.scenes]
        else:
            path = self.srt_path
            if not path:
                path, _ = QFileDialog.getOpenFileName(
                    self, "Chon file SRT can clean", "", "SRT (*.srt);;Tat ca (*)")
            if not path:
                return

            entries = parse_srt(path)
            if not entries:
                QMessageBox.warning(self, "Loi", "Khong doc duoc SRT hoac file rong.")
                return

            lines = [clean_export_line(entry.text) for entry in entries]
            export_mode = "srt_fallback"
            title_source_path = path

        if not any(lines):
            QMessageBox.warning(self, "Thong bao", "Khong co noi dung nao de xuat.")
            return

        # Tạo file Word
        doc = DocxDocument()

        # Style cơ bản
        style = doc.styles['Normal']
        style.font.name  = 'Arial'
        style.font.size  = Pt(12)

        # Thêm từng dòng text
        for line in lines:
            p = doc.add_paragraph(line)
            p.style = doc.styles['Normal']
            p.paragraph_format.space_after = Pt(4)

        # Lưu
        if title_source_path:
            suffix = "_scene_clean.docx" if export_mode == "scene" else "_clean.docx"
            default_out = os.path.splitext(title_source_path)[0] + suffix
        else:
            default_out = "scene_clean.docx"
        out_path, _ = QFileDialog.getSaveFileName(
            self, "Luu file Word", default_out, "Word Document (*.docx)")
        if not out_path:
            return

        doc.save(out_path)
        QMessageBox.information(
            self, "Hoan thanh",
            f"Da xuat {len(lines)} dong\n-> {out_path}")
        if export_mode == "scene":
            self.status.showMessage(
                f"Clean theo scene: {len(lines)} dong -> {os.path.basename(out_path)}")
        else:
            self.status.showMessage(
                f"Clean tu SRT (gom theo nhịp): {len(lines)} dong -> {os.path.basename(out_path)}")

    # ── Save Images ───────────────────────────────────────────────

    def save_images(self):
        if not self.scenes:
            QMessageBox.information(self, "Thong bao", "Chua co canh nao de luu.")
            return

        sample_image = None
        if 0 <= self.current_idx < len(self.scenes):
            sample_image = self.scenes[self.current_idx].thumbnail
        if sample_image is None:
            for scene in self.scenes:
                if scene.thumbnail is not None:
                    sample_image = scene.thumbnail
                    break

        dlg = SaveImagesDialog(sample_image=sample_image, parent=self)
        if dlg.exec_() != QDialog.Accepted:
            return
        if not dlg.output_dir:
            QMessageBox.warning(self, "Chua chon thu muc", "Vui long chon thu muc luu anh.")
            return

        total_to_save = sum(
            1 for sc in self.scenes
            if sc.thumbnail is not None
            and (not dlg.only_with_sub or sc.subs)
        )
        if total_to_save == 0:
            QMessageBox.information(self, "Thong bao", "Khong co anh nao thoa dieu kien.")
            return

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.btn_save_imgs.setEnabled(False)

        self.save_worker = SaveImagesWorker(
            scenes       = self.scenes,
            output_dir   = dlg.output_dir,
            img_format   = dlg.img_format,
            only_with_sub = dlg.only_with_sub,
            zoom_pct     = dlg.zoom_pct,
            offset_x_pct = dlg.offset_x_pct,
            offset_y_pct = dlg.offset_y_pct
        )
        self.save_worker.progress.connect(self.on_progress)
        self.save_worker.finished.connect(self.on_save_images_done)
        self.save_worker.error.connect(self.on_save_images_error)
        self.save_worker.start()

    def on_save_images_done(self, saved: int):
        self.progress_bar.setVisible(False)
        self.btn_save_imgs.setEnabled(True)
        QMessageBox.information(
            self, "Hoan thanh",
            f"Da luu {saved} anh thanh cong!")
        self.status.showMessage(f"Da luu {saved} anh.")

    def on_save_images_error(self, msg: str):
        self.progress_bar.setVisible(False)
        self.btn_save_imgs.setEnabled(True)
        QMessageBox.critical(self, "Loi", f"Loi khi luu anh:\n{msg}")

    # ── Export SRT ────────────────────────────────────────────────

    def export_srt(self):
        if self.current_idx >= 0:
            self._sync_edited_text(self.current_idx)

        default_name = ""
        if self.srt_path:
            default_name = os.path.splitext(self.srt_path)[0] + "_merged.srt"

        path, _ = QFileDialog.getSaveFileName(
            self, "Luu SRT", default_name, "SRT (*.srt)")
        if not path:
            return

        final_scenes = []
        for i, scene in enumerate(self.scenes):
            sc = deepcopy(scene)
            if i == self.current_idx:
                txt = self.txt_sub.toPlainText().strip()
                if txt != sc.merged_text:
                    sc.subs = [SubEntry(0, sc.start_ms, sc.end_ms, txt)] if txt else []
            final_scenes.append(sc)

        try:
            export_srt(final_scenes, path)
            exported = sum(1 for s in final_scenes if s.merged_text)
            QMessageBox.information(
                self, "Hoan thanh",
                f"Da xuat {exported} dong subtitle\n-> {path}")
            self.status.showMessage(f"Da xuat: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.critical(self, "Loi", f"Khong luu duoc file:\n{e}")

    def _sync_edited_text(self, idx):
        if idx < 0 or idx >= len(self.scenes):
            return
        text  = self.txt_sub.toPlainText().strip()
        scene = self.scenes[idx]
        if text != scene.merged_text:
            scene.subs = [SubEntry(0, scene.start_ms, scene.end_ms, text)] if text else []

    # ── App Update ────────────────────────────────────────────────

    def check_for_updates(self):
        if not GITHUB_REPO.strip():
            QMessageBox.information(
                self, "Chua cau hinh GitHub Releases",
                "Ban can sua bien GITHUB_REPO trong code theo dang:\n"
                "owner/repo\n\n"
                "Vi du: yourname/comic-sub-tool")
            return

        self.btn_update.setEnabled(False)
        self.status.showMessage("Dang kiem tra GitHub Releases...")
        preferred_asset_name = UPDATE_ASSET_NAME if getattr(sys, "frozen", False) else SOURCE_UPDATE_ASSET_NAME
        self.update_worker = UpdateCheckWorker(GITHUB_REPO, preferred_asset_name)
        self.update_worker.finished.connect(self.on_update_manifest_loaded)
        self.update_worker.error.connect(self.on_update_check_error)
        self.update_worker.start()

    def on_update_manifest_loaded(self, manifest: dict):
        self.btn_update.setEnabled(True)
        latest_version = str(manifest.get("version", "")).strip()
        asset_url = str(manifest.get("asset_url", "")).strip()
        notes = str(manifest.get("notes", "")).strip()
        if not latest_version or not asset_url:
            QMessageBox.warning(
                self, "Release khong hop le",
                "Khong tim thay asset update trong GitHub Release moi nhat.\n"
                "Hay kiem tra ten file UPDATE_ASSET_NAME.")
            self.status.showMessage("GitHub Release moi nhat chua co asset hop le.")
            return

        if not is_newer_version(latest_version, APP_VERSION):
            QMessageBox.information(
                self, "Da moi nhat",
                f"Ban dang o phien ban moi nhat: v{APP_VERSION}")
            self.status.showMessage(f"Da moi nhat: v{APP_VERSION}")
            return

        note_text = f"\n\nGhi chu:\n{notes}" if notes else ""
        answer = QMessageBox.question(
            self, "Co ban cap nhat moi",
            f"Phien ban hien tai: v{APP_VERSION}\n"
            f"Phien ban moi: v{latest_version}{note_text}\n\n"
            "Ban co muon tai va cai cap nhat khong?"
        )
        if answer != QMessageBox.Yes:
            self.status.showMessage("Da huy cap nhat.")
            return

        try:
            self.download_and_apply_update(manifest)
        except Exception as e:
            QMessageBox.critical(self, "Loi cap nhat", str(e))
            self.status.showMessage("Cap nhat that bai.")

    def on_update_check_error(self, msg: str):
        self.btn_update.setEnabled(True)
        QMessageBox.warning(self, "Khong kiem tra duoc GitHub Releases", msg)
        self.status.showMessage("Khong kiem tra duoc GitHub Releases.")

    def download_and_apply_update(self, manifest: dict):
        asset_url = str(manifest.get("asset_url", "")).strip()
        asset_name = str(manifest.get("asset_name", "")).strip() or os.path.basename(asset_url)
        if not asset_name:
            asset_name = "update_package.bin"

        self.status.showMessage(f"Dang tai ban cap nhat {asset_name}...")
        req = urllib.request.Request(
            asset_url,
            headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()

        if getattr(sys, "frozen", False) and asset_name.lower().endswith(".zip"):
            self._install_zip_update(asset_name, data)
            return

        if (not getattr(sys, "frozen", False)) and asset_name.lower().endswith(".py"):
            self._install_source_update(asset_name, data)
            return

        default_path = os.path.join(os.getcwd(), asset_name)
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Luu goi cap nhat", default_path, "Tat ca (*)")
        if not save_path:
            self.status.showMessage("Da huy luu goi cap nhat.")
            return

        with open(save_path, "wb") as f:
            f.write(data)
        QMessageBox.information(
            self, "Da tai xong",
            f"Da tai goi cap nhat ve:\n{save_path}\n\n"
            "Ban co the dung file nay de cap nhat thu cong hoac build lai app.")
        self.status.showMessage(f"Da tai goi cap nhat: {os.path.basename(save_path)}")

    def _install_zip_update(self, asset_name: str, data: bytes):
        current_exe = os.path.abspath(sys.executable)
        current_pid = os.getpid()
        current_app_dir = os.path.dirname(current_exe)
        current_parent_dir = os.path.dirname(current_app_dir)
        current_app_name = os.path.basename(current_app_dir)
        temp_dir = tempfile.mkdtemp(prefix="comic_sub_update_")
        downloaded_zip = os.path.join(temp_dir, asset_name)
        with open(downloaded_zip, "wb") as f:
            f.write(data)

        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(downloaded_zip, "r") as zf:
            zf.extractall(extract_dir)

        extracted_app_dir = os.path.join(extract_dir, current_app_name)
        if not os.path.isdir(extracted_app_dir):
            raise RuntimeError(
                f"Goi cap nhat khong hop le. File zip phai chua thu muc {current_app_name}."
            )

        relaunched_exe = os.path.join(current_app_dir, APP_EXE_NAME)
        backup_dir = current_app_dir + "_old"
        script_path = os.path.join(temp_dir, "apply_update.bat")
        script = (
            "@echo off\n"
            "setlocal\n"
            f'set "APP_PID={current_pid}"\n'
            f'set "TARGET_DIR={current_app_dir}"\n'
            f'set "TARGET_PARENT={current_parent_dir}"\n'
            f'set "SOURCE_DIR={extracted_app_dir}"\n'
            f'set "BACKUP_DIR={backup_dir}"\n'
            f'set "TARGET_EXE={relaunched_exe}"\n'
            ":wait_for_exit\n"
            'tasklist /FI "PID eq %APP_PID%" | find "%APP_PID%" > nul\n'
            "if not errorlevel 1 (\n"
            "    timeout /t 1 /nobreak > nul\n"
            "    goto wait_for_exit\n"
            ")\n"
            'if exist "%BACKUP_DIR%" rmdir /S /Q "%BACKUP_DIR%" > nul 2>nul\n'
            'if exist "%TARGET_DIR%" move /Y "%TARGET_DIR%" "%BACKUP_DIR%" > nul 2>nul\n'
            'xcopy /E /I /Y "%SOURCE_DIR%" "%TARGET_DIR%" > nul\n'
            "if errorlevel 1 (\n"
            '    if not exist "%TARGET_DIR%" if exist "%BACKUP_DIR%" move /Y "%BACKUP_DIR%" "%TARGET_DIR%" > nul 2>nul\n'
            '    echo Update failed. >> "%TEMP%\\comic_sub_update_error.log"\n'
            "    exit /b 1\n"
            ")\n"
            'start "" /D "%TARGET_DIR%" "%TARGET_EXE%"\n'
            "timeout /t 2 /nobreak > nul\n"
            'if exist "%BACKUP_DIR%" rmdir /S /Q "%BACKUP_DIR%" > nul 2>nul\n'
            'del "%~f0"\n'
        )
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)

        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        subprocess.Popen(
            ["cmd", "/c", script_path],
            creationflags=subprocess.CREATE_NO_WINDOW,
            startupinfo=si
        )
        QMessageBox.information(
            self, "Dang cai cap nhat",
            "App se dong de thay thu muc ung dung moi, sau do tu mo lai.")
        QApplication.instance().quit()

    def _install_source_update(self, asset_name: str, data: bytes):
        current_script = os.path.abspath(sys.argv[0])
        current_python = os.path.abspath(sys.executable)
        temp_dir = tempfile.mkdtemp(prefix="comic_sub_source_update_")
        downloaded_script = os.path.join(temp_dir, asset_name)
        with open(downloaded_script, "wb") as f:
            f.write(data)

        script_path = os.path.join(temp_dir, "apply_source_update.bat")
        script = (
            "@echo off\n"
            "ping 127.0.0.1 -n 3 > nul\n"
            f'copy /Y "{downloaded_script}" "{current_script}" > nul\n'
            f'start "" "{current_python}" "{current_script}"\n'
            'del "%~f0"\n'
        )
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script)

        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        subprocess.Popen(
            ["cmd", "/c", script_path],
            creationflags=subprocess.CREATE_NO_WINDOW,
            startupinfo=si
        )
        QMessageBox.information(
            self, "Dang cai cap nhat source",
            "App se dong de thay file .py moi, sau do tu mo lai.")
        QApplication.instance().quit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current_idx >= 0:
            self.on_scene_selected(self.current_idx)


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(load_app_icon())
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
