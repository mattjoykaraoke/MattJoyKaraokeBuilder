from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


class WaveformData:
    """Precomputed peak envelope for fast zoomable waveform rendering."""

    def __init__(self, peaks: np.ndarray, duration_cs: int, buckets_per_sec: int = 200) -> None:
        self.peaks = peaks  # float32 array in [0.0, 1.0]
        self.duration_cs = duration_cs
        self.buckets_per_sec = buckets_per_sec

    @classmethod
    def from_file(cls, path: str | Path, buckets_per_sec: int = 200) -> "WaveformData":
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        mono = np.abs(data).mean(axis=1)
        total_samples = len(mono)
        duration_sec = total_samples / max(1, sr)
        duration_cs = int(round(duration_sec * 100))

        num_buckets = max(1, int(duration_sec * buckets_per_sec))
        samples_per_bucket = max(1, total_samples // num_buckets)
        trimmed = mono[: num_buckets * samples_per_bucket]
        peaks = trimmed.reshape(num_buckets, samples_per_bucket).max(axis=1)
        max_val = float(peaks.max()) if len(peaks) else 1.0
        if max_val > 1e-6:
            peaks = peaks / max_val
        return cls(peaks=peaks.astype(np.float32), duration_cs=duration_cs, buckets_per_sec=buckets_per_sec)


class AudioEngine(QObject):
    """
    Audio playback (50%/75%/100%) with 60 fps wall-clock interpolated playhead
    so the red playhead glides smoothly even when QMediaPlayer reports coarse ticks.
    """

    position_changed = Signal(int)          # integer centiseconds
    position_changed_float = Signal(float)  # smooth fractional centiseconds
    state_changed = Signal(bool)            # True = playing
    waveform_loaded = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._output = QAudioOutput(self)
        self._output.setVolume(0.9)
        self._player.setAudioOutput(self._output)

        self.main_path: Optional[Path] = None
        self.vocal_path: Optional[Path] = None
        self.use_vocal_audio: bool = False

        self.main_waveform: Optional[WaveformData] = None
        self.vocal_waveform: Optional[WaveformData] = None
        self.duration_cs: int = 0

        # Smooth interpolation state
        self._rate: float = 1.0
        self._anchor_pos_ms: float = 0.0
        self._anchor_wall: float = time.perf_counter()
        self._smooth_cs: float = 0.0

        self._stop_at_cs: Optional[float] = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(16)  # ~60 fps
        self._poll_timer.timeout.connect(self._on_poll)

        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.positionChanged.connect(self._on_backend_position)

    def _reanchor(self, pos_ms: float) -> None:
        self._anchor_pos_ms = max(0.0, pos_ms)
        self._anchor_wall = time.perf_counter()
        self._smooth_cs = self._anchor_pos_ms / 10.0

    def _on_backend_position(self, pos_ms: int) -> None:
        if not self.is_playing:
            self._reanchor(float(pos_ms))
            return
        # Re-anchor only if backend jumped significantly (> 60 ms drift) so we stay smooth & synced
        now = time.perf_counter()
        predicted_ms = self._anchor_pos_ms + (now - self._anchor_wall) * 1000.0 * self._rate
        if abs(pos_ms - predicted_ms) > 60.0:
            self._anchor_pos_ms = float(pos_ms)
            self._anchor_wall = now

    def load_main(self, path: str | Path) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        self.main_path = p
        try:
            self.main_waveform = WaveformData.from_file(p)
            self.duration_cs = self.main_waveform.duration_cs
        except Exception:
            self.main_waveform = None
        if not self.use_vocal_audio:
            self._player.setSource(QUrl.fromLocalFile(str(p)))
        self.waveform_loaded.emit()
        return True

    def load_vocal_stem(self, path: str | Path) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        self.vocal_path = p
        try:
            self.vocal_waveform = WaveformData.from_file(p)
            if self.duration_cs == 0:
                self.duration_cs = self.vocal_waveform.duration_cs
        except Exception:
            self.vocal_waveform = None
        self.waveform_loaded.emit()
        return True

    def set_use_vocal_audio(self, enabled: bool) -> None:
        pos_ms = int(round(self.smooth_position_cs * 10.0))
        was_playing = self.is_playing
        if enabled and self.vocal_path and self.vocal_path.exists():
            self.use_vocal_audio = True
            self._player.setSource(QUrl.fromLocalFile(str(self.vocal_path)))
            self._player.setPosition(pos_ms)
            self._reanchor(float(pos_ms))
            if was_playing:
                self._player.play()
        elif not enabled and self.main_path and self.main_path.exists():
            self.use_vocal_audio = False
            self._player.setSource(QUrl.fromLocalFile(str(self.main_path)))
            self._player.setPosition(pos_ms)
            self._reanchor(float(pos_ms))
            if was_playing:
                self._player.play()

    @property
    def active_waveform(self) -> Optional[WaveformData]:
        if self.vocal_waveform is not None and self.use_vocal_audio:
            return self.vocal_waveform
        return self.main_waveform or self.vocal_waveform

    @property
    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def smooth_position_cs(self) -> float:
        if self.is_playing:
            elapsed_ms = (time.perf_counter() - self._anchor_wall) * 1000.0 * self._rate
            return max(0.0, (self._anchor_pos_ms + elapsed_ms) / 10.0)
        return self._smooth_cs

    @property
    def position_cs(self) -> int:
        return int(round(self.smooth_position_cs))

    def set_position_cs(self, cs: float) -> None:
        cs = max(0.0, float(cs))
        pos_ms = cs * 10.0
        self._player.setPosition(int(round(pos_ms)))
        self._reanchor(pos_ms)
        self.position_changed_float.emit(cs)
        self.position_changed.emit(int(round(cs)))

    def set_playback_rate(self, rate: float) -> None:
        # Preserve current interpolated position before changing rate
        cur_ms = self.smooth_position_cs * 10.0
        self._rate = max(0.1, float(rate))
        self._reanchor(cur_ms)
        self._player.setPlaybackRate(self._rate)

    def set_volume(self, vol: float) -> None:
        self._output.setVolume(max(0.0, min(1.0, vol)))

    def play(self) -> None:
        self._stop_at_cs = None
        self._reanchor(self._smooth_cs * 10.0)
        self._player.play()
        self._poll_timer.start()

    def pause(self) -> None:
        cur_ms = self.smooth_position_cs * 10.0
        self._stop_at_cs = None
        self._player.pause()
        self._poll_timer.stop()
        self._reanchor(cur_ms)

    def toggle_play(self) -> None:
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def play_range(self, start_cs: int, end_cs: int) -> None:
        self._stop_at_cs = float(max(start_cs + 1, end_cs))
        pos_ms = max(0, start_cs) * 10
        self._player.setPosition(pos_ms)
        self._reanchor(float(pos_ms))
        self._player.play()
        self._poll_timer.start()

    def _on_poll(self) -> None:
        cs = self.smooth_position_cs
        self._smooth_cs = cs
        self.position_changed_float.emit(cs)
        self.position_changed.emit(int(round(cs)))
        if self._stop_at_cs is not None and cs >= self._stop_at_cs:
            self.pause()

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        if playing:
            self._anchor_wall = time.perf_counter()
            if not self._poll_timer.isActive():
                self._poll_timer.start()
        else:
            self._poll_timer.stop()
        self.state_changed.emit(playing)
