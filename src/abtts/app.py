from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from abtts.job_worker import JobPlan, JobWorker
from abtts.section_parser import Section, parse_sections_from_epub, parse_sections_from_text


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audiobook TTS (Kokoro) - TXT/EPUB")
        self.resize(980, 620)
        self.setMinimumSize(720, 480)

        self._input_path: Optional[str] = None
        self._sections: List[Section] = []

        self._thread: Optional[QThread] = None
        self._worker: Optional[JobWorker] = None

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

        header = QLabel("Select a file and choose which sections to generate")
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

        self.list_sections = QListWidget()
        self.list_sections.itemChanged.connect(self._update_generate_enabled)
        self.list_sections.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root_layout.addWidget(self.list_sections, 1)

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
        root_layout.addLayout(btn_row)

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

    def _get_output_dir(self) -> str:
        text = self.lbl_out.text()
        prefix = "Output folder: "
        return text[len(prefix):].strip() if text.startswith(prefix) else default_output_dir()

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

        except Exception as e:
            QMessageBox.critical(self, "Error", f"{type(e).__name__}: {e}")

    def _populate_sections_list(self, sections: List[Section]) -> None:
        self.list_sections.blockSignals(True)
        self.list_sections.clear()

        for s in sections:
            if s.kind == "CHAPTER":
                display = s.title
            elif s.kind == "EXTRA":
                display = f"EXTRA {s.title}"
            else:
                display = f"SIDE STORY {s.title}"

            item = QListWidgetItem(display)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Unchecked)
            self.list_sections.addItem(item)

        self.list_sections.blockSignals(False)

    def _select_all(self) -> None:
        for i in range(self.list_sections.count()):
            self.list_sections.item(i).setCheckState(Qt.Checked)

    def _deselect_all(self) -> None:
        for i in range(self.list_sections.count()):
            self.list_sections.item(i).setCheckState(Qt.Unchecked)

    def _select_under_chosen(self) -> None:
        row = self.list_sections.currentRow()
        if row < 0:
            return
        for i in range(row, self.list_sections.count()):
            self.list_sections.item(i).setCheckState(Qt.Checked)

    def _selected_indices(self) -> List[int]:
        indices: List[int] = []
        for i in range(self.list_sections.count()):
            if self.list_sections.item(i).checkState() == Qt.Checked:
                indices.append(i)
        return indices

    def _update_generate_enabled(self) -> None:
        self.btn_generate.setEnabled(len(self._selected_indices()) > 0 and self._input_path is not None)

    def _build_progress_page(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setSpacing(12)

        header = QLabel("Generating...")
        header.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(header)

        layout.addWidget(QLabel("Completed so far"))
        self.list_done = QListWidget()
        layout.addWidget(self.list_done, 1)

        self.lbl_now = QLabel("Now doing: starting...")
        self.lbl_now.setWordWrap(True)
        layout.addWidget(self.lbl_now)

        bottom = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.lbl_stats = QLabel("Chunks 0/0 | 0/0 chars | 0 chars/s | ETA: --")
        self.lbl_stats.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bottom.addWidget(self.progress, 1)
        bottom.addWidget(self.lbl_stats, 0)
        layout.addLayout(bottom)

        self.finished_box = QFrame()
        self.finished_box.setFrameShape(QFrame.StyledPanel)
        self.finished_box.setVisible(False)
        fb = QVBoxLayout(self.finished_box)
        self.lbl_finished = QLabel('Finished! You can find the file(s) in: ""')
        self.lbl_finished.setWordWrap(True)
        self.btn_continue = QPushButton("Continue to generate")
        self.btn_continue.clicked.connect(self._go_to_start)
        fb.addWidget(self.lbl_finished)
        fb.addWidget(self.btn_continue, alignment=Qt.AlignRight)
        layout.addWidget(self.finished_box)
        return root

    def _start_job(self) -> None:
        if not self._input_path or not self._sections:
            return

        selected = self._selected_indices()
        if not selected:
            return

        out_dir = self._get_output_dir()

        self.list_done.clear()
        self.progress.setValue(0)
        self.lbl_stats.setText("Chunks 0/0 | 0/0 chars | 0 chars/s | ETA: --")
        self.lbl_now.setText("Now doing: starting...")
        self.finished_box.setVisible(False)

        self.stack.setCurrentWidget(self.page_progress)

        plan = JobPlan(
            input_path=self._input_path,
            output_dir=out_dir,
            sections=self._sections,
            selected_indices=selected,
        )

        self._thread = QThread()
        self._worker = JobWorker(plan)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.now_doing.connect(self._on_now_doing)
        self._worker.section_done.connect(self._on_section_done)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_progress(
        self,
        processed: int,
        total: int,
        cps: float,
        eta: float,
        done_chunks: int,
        total_chunks: int,
    ) -> None:
        pct = int((processed / max(total, 1)) * 100)
        self.progress.setValue(min(max(pct, 0), 100))
        eta_str = f"{int(eta // 60)}m {int(eta % 60)}s" if eta > 0 else "--"
        self.lbl_stats.setText(
            f"Chunks {done_chunks}/{total_chunks} | {processed}/{total} chars | {cps:.1f} chars/s | ETA: {eta_str}"
        )

    def _on_now_doing(self, msg: str) -> None:
        self.lbl_now.setText(f"Now doing: {msg}")

    def _on_section_done(self, name: str) -> None:
        self.list_done.addItem(name)

    def _on_finished(self, out_dir: str) -> None:
        self.list_done.addItem("All selected sections processed.")
        self.lbl_finished.setText(f'Finished! You can find the file(s) in: "{out_dir}"')
        self.finished_box.setVisible(True)

    def _on_failed(self, msg: str) -> None:
        self.list_done.addItem(f"FAILED: {msg}")
        QMessageBox.critical(self, "Job failed", msg)
        self.lbl_finished.setText(f'Failed. Partial outputs (if any) are in: "{self._get_output_dir()}"')
        self.finished_box.setVisible(True)

    def _go_to_start(self) -> None:
        self.stack.setCurrentWidget(self.page_select)


def run_app() -> None:
    app = QApplication([])
    win = MainWindow()
    win.show()
    app.exec()
