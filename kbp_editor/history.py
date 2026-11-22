from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import List, Optional

from .model import KbpProject


@dataclass
class HistoryEntry:
    label: str
    snapshot: KbpProject


class HistoryManager:
    """Bounded snapshot-based Undo/Redo stack for KbpProject."""

    def __init__(self, initial: Optional[KbpProject] = None, max_entries: int = 150) -> None:
        self._stack: List[HistoryEntry] = []
        self._index: int = -1
        self._saved_index: int = -1
        self._max_entries: int = max_entries
        if initial is not None:
            self.reset(initial)

    def reset(self, project: KbpProject, label: str = "Load Project") -> None:
        self._stack = [HistoryEntry(label=label, snapshot=deepcopy(project))]
        self._index = 0
        self._saved_index = 0

    def push(self, label: str, project: KbpProject) -> None:
        # Discard any redo branch
        if self._index < len(self._stack) - 1:
            self._stack = self._stack[: self._index + 1]
            if self._saved_index > self._index:
                self._saved_index = -1
        self._stack.append(HistoryEntry(label=label, snapshot=deepcopy(project)))
        if self._max_entries > 0 and len(self._stack) > self._max_entries:
            overflow = len(self._stack) - self._max_entries
            del self._stack[:overflow]
            if self._saved_index >= 0:
                self._saved_index -= overflow
                if self._saved_index < 0:
                    self._saved_index = -1
        self._index = len(self._stack) - 1

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return 0 <= self._index < len(self._stack) - 1

    @property
    def undo_label(self) -> str:
        return self._stack[self._index].label if self.can_undo else ""

    @property
    def redo_label(self) -> str:
        return self._stack[self._index + 1].label if self.can_redo else ""

    @property
    def is_dirty(self) -> bool:
        return self._index != self._saved_index

    @property
    def count(self) -> int:
        return len(self._stack)

    @property
    def current_index(self) -> int:
        return self._index

    def mark_saved(self) -> None:
        self._saved_index = self._index

    def mark_dirty(self) -> None:
        self._saved_index = -1

    def undo(self) -> Optional[KbpProject]:
        if not self.can_undo:
            return None
        self._index -= 1
        return deepcopy(self._stack[self._index].snapshot)

    def redo(self) -> Optional[KbpProject]:
        if not self.can_redo:
            return None
        self._index += 1
        return deepcopy(self._stack[self._index].snapshot)

    def history_labels(self) -> List[str]:
        return [entry.label for entry in self._stack]
