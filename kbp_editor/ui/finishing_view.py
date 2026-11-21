from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ..model import KbpProject


class FinishingView(QWidget):
    """
    Placeholder 'Finishing' tab for upcoming post-sync finalization tools
    (quality checks, metadata/header polish, export helpers, etc.).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.project: Optional[KbpProject] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame(self)
        card.setMaximumWidth(560)
        card.setStyleSheet(
            "QFrame { background-color: #20242c; border: 1px solid #353b47; border-radius: 8px; }"
            "QLabel { border: none; }"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(28, 24, 28, 24)
        cl.setSpacing(10)

        title = QLabel("Finishing", card)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #f1f5f9;")
        cl.addWidget(title)

        subtitle = QLabel("Post-sync polish & finalization tools — coming soon.", card)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 13px; color: #94a3b8;")
        cl.addWidget(subtitle)

        self._project_lbl = QLabel("No project loaded.", card)
        self._project_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._project_lbl.setWordWrap(True)
        self._project_lbl.setStyleSheet("font-size: 11px; color: #64748b; margin-top: 8px;")
        cl.addWidget(self._project_lbl)

        root.addWidget(card)

    def set_project(self, project: Optional[KbpProject]) -> None:
        self.project = project
        if project is None:
            self._project_lbl.setText("No project loaded.")
            return
        name = (
            f"{project.header.artist} - {project.header.title}"
            if (project.header.artist and project.header.title)
            else (project.header.title or (project.source_path.name if project.source_path else "Untitled Project"))
        )
        total_syls = sum(len(ln.syllables) for p in project.pages for ln in p.lines)
        self._project_lbl.setText(f"Active project: {name}  •  {len(project.pages)} pages  •  {total_syls} syllables")
