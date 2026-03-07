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

        # Mapping from visible list row -> original section index
        self._visible_to_section_index: List[int] = []

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

        header = QLabel("Select a file and choose which chapters to generate")
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
        """
        Show ONLY CHAPTER items in the selection list.

        IMPORTANT: because we filter the visible list, we maintain a mapping:
        visible row index -> original section index
        """
        self.list_sections.blockSignals(True)
        self.list_sections.clear()
        self._visible_to_section_index = []

        for original_index, s in enumerate(sections):
            if s.kind != "CHAPTER":
                continue  # hide EXTRA and SIDE STORY per your request

            # Keep your display style (chapter index in the file title is already in s.title if you wrote it that way)
            display = s.title

            item = QListWidgetItem(display)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Unchecked)
            self.list_sections.addItem(item)
            self._visible_to_section_index.append(original_index)

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
        """
        Return indices into self._sections (original sections list),
        not indices into the visible list widget.
        """
        indices: List[int] = []
        for visible_row in range(self.list_sections.count()):
            if self.list_sections.item(visible_row).checkState() == Qt.Checked:
                indices.append(self._visible_to_section_index[visible_row])
        return indices

    def _update_generate_enabled(self) -> None:
        self.btn_generate.setEnabled(len(self._selected_indices()) > 0 and self._input_path is not None)

    def _build_progress_page(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setSpacing(10)

        self.lbl_status = QLabel("Ready.")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        self.prog = QProgressBar()
        self.prog.setRange(0, 100)
        self.prog.setValue(0)
        layout.addWidget(self.prog)

        self.lbl_detail = QLabel("")
        self.lbl_detail.setWordWrap(True)
        layout.addWidget(self.lbl_detail)

        btn_row = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self._cancel_job)
        self.btn_back = QPushButton("Back")
        self.btn_back.clicked.connect(self._back_to_select)
        self.btn_back.setEnabled(False)

        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_back)
        layout.addLayout(btn_row)

        return root

    def _start_job(self) -> None:
        if not self._input_path:
            return
        out_dir = self._get_output_dir()
        selected = self._selected_indices()
        if not selected:
            return

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
        self._worker.now_doing.connect(self._on_now_doing)
        self._worker.section_done.connect(self._on_section_done)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)

        self._thread.start()
        self.btn_back.setEnabled(False)
        self.stack.setCurrentWidget(self.page_progress)

    def _cancel_job(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.lbl_status.setText("Cancelling...")

    def _back_to_select(self) -> None:
        self.stack.setCurrentWidget(self.page_select)

    def _on_now_doing(self, text: str) -> None:
        self.lbl_status.setText(text)

    def _on_section_done(self, text: str) -> None:
        # Just show last completed line
        self.lbl_detail.setText(text)

    def _on_progress(self, done_chars: int, total_chars: int, cps: float, eta: float, done_chunks: int, total_chunks: int) -> None:
        if total_chars <= 0:
            total_chars = 1
        pct = int(round((done_chars / total_chars) * 100))
        self.prog.setValue(max(0, min(100, pct)))
        self.lbl_detail.setText(
            f"Text: {done_chars}/{total_chars} chars | Speed: {cps:.1f} chars/s | ETA: {eta:.1f}s | "
            f"Parts: {done_chunks}/{total_chunks}"
        )

    def _cleanup_thread(self) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = None
        self._worker = None

    def _on_finished(self, out_dir: str) -> None:
        self._cleanup_thread()
        self.lbl_status.setText("Finished.")
        self.lbl_detail.setText(f"Output saved to: {out_dir}")
        self.btn_back.setEnabled(True)
        self.btn_cancel.setEnabled(False)

    def _on_failed(self, msg: str) -> None:
        self._cleanup_thread()
        self.lbl_status.setText("Failed.")
        self.lbl_detail.setText(msg)
        self.btn_back.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        QMessageBox.critical(self, "Error", msg)


def main() -> None:
    app = QApplication([])
    w = MainWindow()
    w.show()
    app.exec()


def run_app() -> None:
    main()


if __name__ == "__main__":
    main()
