from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QDoubleSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from abtts.job_worker import GenerationSettings, JobPlan, JobWorker
from abtts.section_parser import Section, parse_sections_from_epub, parse_sections_from_text


VOICE_OPTIONS = [
    "af_heart",
    "af_bella",
    "af_sarah",
    "af_nicole",
    "am_adam",
    "am_michael",
    "bf_emma",
    "bm_george",
]
BITRATE_OPTIONS = ["64k", "96k", "128k", "160k", "192k"]
STATUS_COLORS = {
    "Pending": "#777777",
    "Queued": "#7a5cff",
    "Working": "#c97a00",
    "Paused": "#9a4dff",
    "Done": "#1f8b4c",
    "Failed": "#b12c2c",
}


def default_output_dir() -> str:
    base = Path.home() / "AudiobookTTS" / "Output"
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


class DropZone(QFrame):
    file_dropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("QFrame { border: 2px dashed #888; border-radius: 8px; }")
        self.label = QLabel('Drop a .txt or .epub file here\nor use "Choose file"')
        self.label.setAlignment(Qt.AlignCenter)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path:
            self.file_dropped.emit(path)


class SectionRowWidget(QWidget):
    def __init__(self, title: str):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(10)

        self.checkbox = QCheckBox()
        self.title_label = QLabel(title)
        self.title_label.setWordWrap(True)
        self.status_label = QLabel()
        self.status_label.setMinimumWidth(88)
        self.status_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.checkbox, 0)
        layout.addWidget(self.title_label, 1)
        layout.addWidget(self.status_label, 0)

        self.set_status("Pending")

    def set_status(self, status: str) -> None:
        color = STATUS_COLORS.get(status, "#666666")
        self.status_label.setText(status)
        self.status_label.setStyleSheet(
            f"QLabel {{ background: {color}; color: white; border-radius: 10px; padding: 3px 8px; font-weight: 600; }}"
        )

    def set_checked(self, checked: bool) -> None:
        self.checkbox.setChecked(checked)

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audiobook TTS (Kokoro) - TXT/EPUB")
        self.resize(1120, 760)
        self.setMinimumSize(880, 620)

        self._input_path: Optional[str] = None
        self._sections: List[Section] = []
        self._visible_to_section_index: List[int] = []
        self._row_widgets: Dict[int, SectionRowWidget] = {}
        self._status_notes: Dict[int, str] = {}
        self._thread: Optional[QThread] = None
        self._worker: Optional[JobWorker] = None
        self._is_paused = False
        self._last_output_dir = default_output_dir()

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.page_select = self._build_select_page()
        self.page_progress = self._build_progress_page()
        self.stack.addWidget(self.page_select)
        self.stack.addWidget(self.page_progress)
        self.stack.setCurrentWidget(self.page_select)

    def _build_select_page(self) -> QWidget:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setSpacing(12)

        header = QLabel("Select a file, choose chapters, and tune generation settings")
        header.setStyleSheet("font-size: 18px; font-weight: 600;")
        root_layout.addWidget(header)

        top_row = QHBoxLayout()
        self.drop_zone = DropZone()
        self.drop_zone.file_dropped.connect(self._load_file)
        self.btn_choose = QPushButton("Choose file...")
        self.btn_choose.clicked.connect(self._choose_file)
        top_row.addWidget(self.drop_zone, 1)
        top_row.addWidget(self.btn_choose, 0)
        root_layout.addLayout(top_row)

        self.lbl_file = QLabel("No file selected.")
        self.lbl_file.setWordWrap(True)
        root_layout.addWidget(self.lbl_file)

        middle_row = QHBoxLayout()
        middle_row.setSpacing(12)

        left_col = QVBoxLayout()
        self.list_sections = QListWidget()
        self.list_sections.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_col.addWidget(self.list_sections, 1)

        btn_row = QHBoxLayout()
        self.btn_select_all = QPushButton("Select all")
        self.btn_deselect_all = QPushButton("Deselect all")
        self.btn_select_under = QPushButton("Select under chosen")
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_deselect_all.clicked.connect(self._deselect_all)
        self.btn_select_under.clicked.connect(self._select_under_chosen)
        btn_row.addWidget(self.btn_select_all)
        btn_row.addWidget(self.btn_deselect_all)
        btn_row.addWidget(self.btn_select_under)
        btn_row.addStretch(1)
        left_col.addLayout(btn_row)

        middle_row.addLayout(left_col, 3)
        middle_row.addWidget(self._build_settings_panel(), 2)
        root_layout.addLayout(middle_row, 1)

        bottom_row = QHBoxLayout()
        self.lbl_out = QLabel(f"Output folder: {default_output_dir()}")
        self.lbl_out.setWordWrap(True)
        self.btn_output = QPushButton("Change output folder...")
        self.btn_output.clicked.connect(self._choose_output)
        self.btn_generate = QPushButton("Generate")
        self.btn_generate.setEnabled(False)
        self.btn_generate.clicked.connect(self._start_job)
        bottom_row.addWidget(self.lbl_out, 1)
        bottom_row.addWidget(self.btn_output, 0)
        bottom_row.addWidget(self.btn_generate, 0)
        root_layout.addLayout(bottom_row)
        return root

    def _build_settings_panel(self) -> QWidget:
        box = QFrame()
        box.setFrameShape(QFrame.StyledPanel)
        box.setStyleSheet("QFrame { border: 1px solid #666; border-radius: 10px; }")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self.cmb_voice = QComboBox()
        self.cmb_voice.addItems(VOICE_OPTIONS)
        self.cmb_voice.setCurrentText("af_heart")

        self.cmb_bitrate = QComboBox()
        self.cmb_bitrate.addItems(BITRATE_OPTIONS)
        self.cmb_bitrate.setCurrentText("96k")

        self.spin_chunk = QSpinBox()
        self.spin_chunk.setRange(200, 2000)
        self.spin_chunk.setSingleStep(50)
        self.spin_chunk.setValue(700)
        self.spin_chunk.setSuffix(" chars")

        self.spin_part_silence = QDoubleSpinBox()
        self.spin_part_silence.setRange(0.0, 10.0)
        self.spin_part_silence.setSingleStep(0.05)
        self.spin_part_silence.setDecimals(2)
        self.spin_part_silence.setValue(0.35)
        self.spin_part_silence.setSuffix(" s")

        self.spin_chapter_silence = QDoubleSpinBox()
        self.spin_chapter_silence.setRange(0.0, 30.0)
        self.spin_chapter_silence.setSingleStep(0.1)
        self.spin_chapter_silence.setDecimals(2)
        self.spin_chapter_silence.setValue(0.80)
        self.spin_chapter_silence.setSuffix(" s")

        self.chk_speak_parts = QCheckBox("Speak 'Part N' headers")
        self.chk_embed_chapters = QCheckBox("Embed M4B chapter markers")
        self.chk_embed_chapters.setChecked(True)

        grid.addWidget(QLabel("Voice"), 0, 0)
        grid.addWidget(self.cmb_voice, 0, 1)
        grid.addWidget(QLabel("AAC bitrate"), 1, 0)
        grid.addWidget(self.cmb_bitrate, 1, 1)
        grid.addWidget(QLabel("Chunk size"), 2, 0)
        grid.addWidget(self.spin_chunk, 2, 1)
        grid.addWidget(QLabel("Silence between parts"), 3, 0)
        grid.addWidget(self.spin_part_silence, 3, 1)
        grid.addWidget(QLabel("Silence between chapters"), 4, 0)
        grid.addWidget(self.spin_chapter_silence, 4, 1)
        layout.addLayout(grid)
        layout.addWidget(self.chk_speak_parts)
        layout.addWidget(self.chk_embed_chapters)
        layout.addStretch(1)
        return box

    def _build_progress_page(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setSpacing(10)

        title = QLabel("Generation Progress")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        self.lbl_status = QLabel("Ready.")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        current_box = QFrame()
        current_box.setFrameShape(QFrame.StyledPanel)
        current_box.setStyleSheet("QFrame { border: 1px solid #666; border-radius: 8px; }")
        current_layout = QVBoxLayout(current_box)
        current_layout.setContentsMargins(10, 10, 10, 10)
        current_layout.addWidget(QLabel("Current task"))
        self.lbl_current_task = QLabel("Waiting to start...")
        self.lbl_current_task.setWordWrap(True)
        current_layout.addWidget(self.lbl_current_task)
        layout.addWidget(current_box)

        center_row = QHBoxLayout()
        center_row.setSpacing(12)

        finished_box = QFrame()
        finished_box.setFrameShape(QFrame.StyledPanel)
        finished_box.setStyleSheet("QFrame { border: 1px solid #666; border-radius: 8px; }")
        finished_layout = QVBoxLayout(finished_box)
        finished_layout.setContentsMargins(10, 10, 10, 10)
        finished_layout.addWidget(QLabel("Finished chapters"))
        self.list_finished = QListWidget()
        finished_layout.addWidget(self.list_finished, 1)
        center_row.addWidget(finished_box, 3)

        log_box = QFrame()
        log_box.setFrameShape(QFrame.StyledPanel)
        log_box.setStyleSheet("QFrame { border: 1px solid #666; border-radius: 8px; }")
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(10, 10, 10, 10)
        log_layout.addWidget(QLabel("Log / debug"))
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setLineWrapMode(QTextEdit.NoWrap)
        log_layout.addWidget(self.txt_log, 1)
        log_btns = QHBoxLayout()
        self.btn_export_log = QPushButton("Export log...")
        self.btn_export_log.clicked.connect(self._export_log)
        self.btn_clear_log = QPushButton("Clear")
        self.btn_clear_log.clicked.connect(self.txt_log.clear)
        log_btns.addWidget(self.btn_export_log)
        log_btns.addWidget(self.btn_clear_log)
        log_btns.addStretch(1)
        log_layout.addLayout(log_btns)
        center_row.addWidget(log_box, 2)

        layout.addLayout(center_row, 1)

        self.lbl_detail = QLabel("Text: 0/0 chars | Speed: 0.0 chars/s | ETA: 0.0s | Chunks: 0/0")
        self.lbl_detail.setWordWrap(True)
        layout.addWidget(self.lbl_detail)

        self.prog = QProgressBar()
        self.prog.setRange(0, 100)
        self.prog.setValue(0)
        self.prog.setTextVisible(True)
        self.prog.setMinimumHeight(28)
        self.prog.setStyleSheet(
            "QProgressBar { border: 1px solid #666; border-radius: 8px; text-align: center; min-height: 28px; font-weight: 600; }"
            "QProgressBar::chunk { border-radius: 7px; }"
        )
        layout.addWidget(self.prog)

        btn_row = QHBoxLayout()
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._cancel_job)
        self.btn_open_output = QPushButton("Open output folder")
        self.btn_open_output.clicked.connect(self._open_output_folder)
        self.btn_open_output.setEnabled(False)
        self.btn_back = QPushButton("Back")
        self.btn_back.clicked.connect(self._back_to_select)
        self.btn_back.setEnabled(False)
        btn_row.addWidget(self.btn_pause)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_open_output)
        btn_row.addWidget(self.btn_back)
        layout.addLayout(btn_row)
        return root

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose TXT or EPUB file",
            str(Path.home()),
            "Supported files (*.txt *.epub);;Text files (*.txt);;EPUB files (*.epub);;All files (*.*)",
        )
        if path:
            self._load_file(path)

    def _choose_output(self) -> None:
        current = self._get_output_dir()
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder", current)
        if folder:
            self.lbl_out.setText(f"Output folder: {folder}")
            self._last_output_dir = folder

    def _get_output_dir(self) -> str:
        text = self.lbl_out.text()
        prefix = "Output folder: "
        return text[len(prefix):].strip() if text.startswith(prefix) else default_output_dir()

    def _build_settings(self) -> GenerationSettings:
        return GenerationSettings(
            voice=self.cmb_voice.currentText().strip() or "af_heart",
            part_silence_s=float(self.spin_part_silence.value()),
            chapter_silence_s=float(self.spin_chapter_silence.value()),
            aac_bitrate=self.cmb_bitrate.currentText().strip() or "96k",
            chunk_size=int(self.spin_chunk.value()),
            speak_part_headers=self.chk_speak_parts.isChecked(),
            embed_m4b_chapters=self.chk_embed_chapters.isChecked(),
        )

    def _load_file(self, path: str) -> None:
        try:
            p = Path(path)
            if not p.exists():
                QMessageBox.warning(self, "File not found", "That file path does not exist.")
                return

            ext = p.suffix.lower()
            if ext not in {".txt", ".epub"}:
                QMessageBox.warning(self, "Unsupported file", "Please select a .txt or .epub file.")
                return

            self._input_path = str(p)
            self.lbl_file.setText(f"Selected file: {self._input_path}")

            if ext == ".epub":
                self._sections = parse_sections_from_epub(str(p))
            else:
                content = p.read_text(encoding="utf-8", errors="ignore")
                self._sections = parse_sections_from_text(content)

            self._populate_sections_list(self._sections)
            self._update_generate_enabled()
            self._append_log(f"Loaded file: {self._input_path}")
            self._append_log(f"Parsed sections: {len(self._sections)}")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"{type(e).__name__}: {e}")

    def _populate_sections_list(self, sections: List[Section]) -> None:
        self.list_sections.clear()
        self._visible_to_section_index = []
        self._row_widgets = {}
        self._status_notes = {}

        for original_index, s in enumerate(sections):
            if s.kind != "CHAPTER":
                continue

            item = QListWidgetItem()
            item.setSizeHint(item.sizeHint().expandedTo(self.list_sections.sizeHintForIndex(self.list_sections.model().index(0, 0))))
            widget = SectionRowWidget(s.title)
            widget.checkbox.toggled.connect(self._update_generate_enabled)

            self.list_sections.addItem(item)
            self.list_sections.setItemWidget(item, widget)
            item.setSizeHint(widget.sizeHint())

            self._visible_to_section_index.append(original_index)
            self._row_widgets[original_index] = widget
            self._status_notes[original_index] = ""

    def _set_section_status(self, section_index: int, status: str, note: str = "") -> None:
        widget = self._row_widgets.get(section_index)
        if widget:
            widget.set_status(status)
            tooltip = f"{status}: {note}" if note else status
            widget.status_label.setToolTip(tooltip)
        self._status_notes[section_index] = note

    def _select_all(self) -> None:
        for widget in self._row_widgets.values():
            widget.set_checked(True)
        self._update_generate_enabled()

    def _deselect_all(self) -> None:
        for widget in self._row_widgets.values():
            widget.set_checked(False)
        self._update_generate_enabled()

    def _select_under_chosen(self) -> None:
        row = self.list_sections.currentRow()
        if row < 0:
            return
        for i in range(row, self.list_sections.count()):
            section_index = self._visible_to_section_index[i]
            widget = self._row_widgets.get(section_index)
            if widget:
                widget.set_checked(True)
        self._update_generate_enabled()

    def _selected_indices(self) -> List[int]:
        indices: List[int] = []
        for section_index in self._visible_to_section_index:
            widget = self._row_widgets.get(section_index)
            if widget and widget.is_checked():
                indices.append(section_index)
        return indices

    def _update_generate_enabled(self) -> None:
        self.btn_generate.setEnabled(bool(self._input_path) and len(self._selected_indices()) > 0)

    def _reset_progress_ui(self) -> None:
        self.lbl_status.setText("Preparing...")
        self.lbl_current_task.setText("Waiting to start...")
        self.lbl_detail.setText("Text: 0/0 chars | Speed: 0.0 chars/s | ETA: 0.0s | Chunks: 0/0")
        self.prog.setValue(0)
        self.list_finished.clear()
        self.txt_log.clear()
        self.btn_cancel.setEnabled(True)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("Pause")
        self.btn_back.setEnabled(False)
        self.btn_open_output.setEnabled(False)
        self._is_paused = False

    def _start_job(self) -> None:
        if not self._input_path:
            return
        out_dir = self._get_output_dir()
        selected = self._selected_indices()
        if not selected:
            return

        self._last_output_dir = out_dir
        self._reset_progress_ui()

        for idx in self._visible_to_section_index:
            if idx in selected:
                self._set_section_status(idx, "Queued", "Waiting to start")
            else:
                self._set_section_status(idx, "Pending", "Not selected")

        settings = self._build_settings()
        plan = JobPlan(
            input_path=self._input_path,
            output_dir=out_dir,
            sections=self._sections,
            selected_indices=selected,
            settings=settings,
        )

        self._thread = QThread()
        self._worker = JobWorker(plan)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.now_doing.connect(self._on_now_doing)
        self._worker.section_done.connect(self._on_section_done)
        self._worker.section_status.connect(self._on_section_status)
        self._worker.progress.connect(self._on_progress)
        self._worker.paused.connect(self._on_paused_state_changed)
        self._worker.log.connect(self._append_log)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

        self._thread.start()
        self.stack.setCurrentWidget(self.page_progress)

    def _cancel_job(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.lbl_status.setText("Cancelling...")
            self.lbl_current_task.setText("Stopping current work safely...")
            self.btn_cancel.setEnabled(False)
            self.btn_pause.setEnabled(False)

    def _toggle_pause(self) -> None:
        if not self._worker:
            return
        pause_now = not self._is_paused
        self._worker.set_paused(pause_now)
        self.btn_pause.setText("Resume" if pause_now else "Pause")
        self.lbl_current_task.setText("Pause requested..." if pause_now else "Resuming...")

        current_row = self.list_sections.currentRow()
        if current_row >= 0:
            section_index = self._visible_to_section_index[current_row]
            if pause_now:
                self._set_section_status(section_index, "Paused", "Paused by user")

    def _back_to_select(self) -> None:
        self.stack.setCurrentWidget(self.page_select)

    def _on_now_doing(self, text: str) -> None:
        self.lbl_status.setText(text)
        self.lbl_current_task.setText(text)

    def _on_section_done(self, text: str) -> None:
        self.list_finished.addItem(text)
        self.list_finished.scrollToBottom()

    def _on_section_status(self, section_index: int, status: str, detail: str) -> None:
        self._set_section_status(section_index, status, detail)

    def _on_progress(self, done_chars: int, total_chars: int, cps: float, eta: float, done_chunks: int, total_chunks: int) -> None:
        if total_chars <= 0:
            total_chars = 1
        pct = int(round((done_chars / total_chars) * 100))
        self.prog.setValue(max(0, min(100, pct)))
        self.lbl_detail.setText(
            f"Text: {done_chars}/{total_chars} chars | Speed: {cps:.1f} chars/s | ETA: {eta:.1f}s | Chunks: {done_chunks}/{total_chunks}"
        )

    def _on_paused_state_changed(self, paused: bool) -> None:
        self._is_paused = paused
        self.btn_pause.setText("Resume" if paused else "Pause")
        self.lbl_status.setText("Paused." if paused else self.lbl_status.text())
        self._append_log("Paused." if paused else "Resumed.")

    def _cleanup_thread(self) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = None
        self._worker = None

    def _on_finished(self, out_dir: str) -> None:
        self._cleanup_thread()
        self._last_output_dir = out_dir
        self.lbl_status.setText("Finished.")
        self.lbl_current_task.setText(f"Output saved to: {out_dir}")
        self.btn_back.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.btn_open_output.setEnabled(True)
        self.prog.setValue(100)
        self._append_log(f"Job finished. Output folder: {out_dir}")

    def _on_failed(self, msg: str) -> None:
        self._cleanup_thread()
        self.lbl_status.setText("Failed.")
        self.lbl_current_task.setText(msg)
        self.btn_back.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_pause.setEnabled(False)
        self.btn_open_output.setEnabled(Path(self._last_output_dir).exists())
        self._append_log(f"Job failed: {msg}")
        QMessageBox.critical(self, "Error", msg)

    def _append_log(self, text: str) -> None:
        self.txt_log.append(text)

    def _export_log(self) -> None:
        default_name = Path(self._last_output_dir) / "audiobook_tts_debug_log.txt"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export debug log",
            str(default_name),
            "Text files (*.txt);;All files (*.*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(self.txt_log.toPlainText(), encoding="utf-8")
            self._append_log(f"Exported log to: {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", f"{type(e).__name__}: {e}")

    def _open_output_folder(self) -> None:
        folder = Path(self._last_output_dir)
        if not folder.exists():
            QMessageBox.warning(self, "Folder missing", "The output folder does not exist yet.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as e:
            QMessageBox.critical(self, "Open failed", f"{type(e).__name__}: {e}")


def main() -> None:
    app = QApplication([])
    w = MainWindow()
    w.show()
    app.exec()



def run_app() -> None:
    main()


if __name__ == "__main__":
    main()
