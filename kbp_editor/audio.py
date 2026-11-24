from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
from PySide6.QtCore import QIODevice, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioOutput, QAudioSink, QMediaDevices, QMediaPlayer


class WaveformData:
    """Precomputed peak envelope + compact mono int16 PCM buffer for low-latency audio scrubbing."""

    def __init__(
        self,
        peaks: np.ndarray,
        duration_cs: int,
        buckets_per_sec: int = 200,
        scrub_pcm: Optional[np.ndarray] = None,
        scrub_sr: int = 22050,
    ) -> None:
        self.peaks = peaks  # float32 array in [0.0, 1.0]
        self.duration_cs = duration_cs
        self.buckets_per_sec = buckets_per_sec
        self.scrub_pcm = scrub_pcm  # optional 1D int16 mono PCM for instant scrubbing
        self.scrub_sr = scrub_sr

    @classmethod
    def from_file(cls, path: str | Path, buckets_per_sec: int = 200) -> "WaveformData":
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        mono_signed = data.mean(axis=1)
        mono = np.abs(mono_signed)
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

        # Compact mono int16 PCM (~22 kHz) for <5 ms latency audio scrubbing
        if sr >= 40000:
            pcm_f = mono_signed[::2]
            scrub_sr = sr // 2
        else:
            pcm_f = mono_signed
            scrub_sr = sr
        scrub_pcm = np.clip(pcm_f * 32767.0, -32768.0, 32767.0).astype(np.int16)

        return cls(
            peaks=peaks.astype(np.float32),
            duration_cs=duration_cs,
            buckets_per_sec=buckets_per_sec,
            scrub_pcm=scrub_pcm,
            scrub_sr=int(scrub_sr),
        )


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

        # Smooth monotonic PLL interpolation state
        self._rate: float = 1.0
        self._clock_ms: float = 0.0
        self._last_tick_wall: float = time.perf_counter()
        self._backend_pos_ms: float = 0.0
        self._backend_wall: float = time.perf_counter()
        self._smooth_cs: float = 0.0
        self._awaiting_audio_start: bool = False
        self._play_start_wall: float = 0.0
        self._play_start_pos_ms: float = 0.0

        # Seek-guard & debounced backend seek so rapid backward scrubbing never causes stale jumps
        self._seek_target_ms: float = 0.0
        self._seek_guard_until: float = 0.0
        self._pending_seek_ms: Optional[int] = None
        self._seek_debounce_timer = QTimer(self)
        self._seek_debounce_timer.setSingleShot(True)
        self._seek_debounce_timer.setInterval(35)
        self._seek_debounce_timer.timeout.connect(self._flush_pending_seek)

        # Low-latency scrub audio output
        self.scrub_audio_enabled: bool = False
        self._scrub_sink: Optional[QAudioSink] = None
        self._scrub_io: Optional[QIODevice] = None
        self._scrub_sink_sr: int = 0
        self._last_scrub_grain_wall: float = 0.0

        self._stop_at_cs: Optional[float] = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._poll_timer.setInterval(16)  # ~60 fps
        self._poll_timer.timeout.connect(self._on_poll)

        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.positionChanged.connect(self._on_backend_position)

    def _reanchor(self, pos_ms: float) -> None:
        now = time.perf_counter()
        pos = max(0.0, float(pos_ms))
        self._clock_ms = pos
        self._backend_pos_ms = pos
        self._backend_wall = now
        self._last_tick_wall = now
        self._smooth_cs = pos / 10.0

    def _flush_pending_seek(self) -> None:
        if self._pending_seek_ms is not None:
            target = self._pending_seek_ms
            self._pending_seek_ms = None
            self._seek_target_ms = float(target)
            self._seek_guard_until = time.perf_counter() + 0.22
            self._player.setPosition(target)

    def _on_backend_position(self, pos_ms: int) -> None:
        now = time.perf_counter()
        # Ignore stale pre-seek callbacks emitted asynchronously by Windows media backend
        if now < self._seek_guard_until and abs(pos_ms - self._seek_target_ms) > 120.0:
            return
        if not self.is_playing:
            # While paused, user's scrub position is authoritative; don't let stale backend ticks jump it
            self._backend_pos_ms = float(pos_ms)
            self._backend_wall = now
            return
        self._backend_pos_ms = float(pos_ms)
        self._backend_wall = now
        if self._awaiting_audio_start:
            # Only unlock once backend actually advances forward near our start target
            delta = pos_ms - self._play_start_pos_ms
            if 1.0 <= delta <= 250.0:
                self._awaiting_audio_start = False
                self._clock_ms = float(pos_ms)
                self._last_tick_wall = now
                self._smooth_cs = self._clock_ms / 10.0

    def clear_main(self) -> None:
        """Clear any previously loaded main audio and waveform."""
        if self.is_playing and not self.use_vocal_audio:
            self.pause()
        self.main_path = None
        self.main_waveform = None
        if not self.use_vocal_audio:
            self._player.setSource(QUrl())
        self.duration_cs = self.vocal_waveform.duration_cs if self.vocal_waveform is not None else 0
        self.waveform_loaded.emit()

    def clear_vocal(self) -> None:
        """Clear any previously loaded vocal stem audio and waveform."""
        if self.is_playing and self.use_vocal_audio:
            self.pause()
        self.vocal_path = None
        self.vocal_waveform = None
        if self.use_vocal_audio:
            self.use_vocal_audio = False
            if self.main_path and self.main_path.exists():
                self._player.setSource(QUrl.fromLocalFile(str(self.main_path)))
            else:
                self._player.setSource(QUrl())
        self.duration_cs = self.main_waveform.duration_cs if self.main_waveform is not None else 0
        self.waveform_loaded.emit()

    def load_main(self, path: str | Path) -> bool:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return False
        try:
            wf = WaveformData.from_file(p)
        except Exception:
            return False
        self.main_path = p
        self.main_waveform = wf
        self.duration_cs = wf.duration_cs
        if not self.use_vocal_audio:
            self._player.setSource(QUrl.fromLocalFile(str(p)))
        self.waveform_loaded.emit()
        return True

    def load_vocal_stem(self, path: str | Path) -> bool:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return False
        try:
            wf = WaveformData.from_file(p)
        except Exception:
            return False
        self.vocal_path = p
        self.vocal_waveform = wf
        if self.duration_cs == 0:
            self.duration_cs = wf.duration_cs
        if self.use_vocal_audio:
            self._player.setSource(QUrl.fromLocalFile(str(p)))
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
        return (
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            or self._poll_timer.isActive()
        )

    @property
    def smooth_position_cs(self) -> float:
        if self.is_playing:
            return max(0.0, self._clock_ms / 10.0)
        return self._smooth_cs

    @property
    def position_cs(self) -> int:
        return int(round(self.smooth_position_cs))

    def set_scrub_audio_enabled(self, enabled: bool) -> None:
        self.scrub_audio_enabled = bool(enabled)
        if not self.scrub_audio_enabled:
            self._stop_scrub_sink()

    def _stop_scrub_sink(self) -> None:
        if self._scrub_sink is not None:
            try:
                self._scrub_sink.stop()
            except Exception:
                pass
            self._scrub_io = None

    def _ensure_scrub_sink(self, sr: int) -> Optional[QIODevice]:
        if sr <= 0:
            return None
        if self._scrub_sink is not None and self._scrub_sink_sr == sr and self._scrub_io is not None:
            return self._scrub_io
        self._stop_scrub_sink()
        try:
            dev = QMediaDevices.defaultAudioOutput()
            if dev.isNull():
                return None
            fmt = QAudioFormat()
            fmt.setSampleRate(sr)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            self._scrub_sink = QAudioSink(dev, fmt, self)
            self._scrub_sink.setVolume(self._output.volume())
            self._scrub_sink.setBufferSize(max(2048, int(sr * 2 * 0.12)))
            self._scrub_io = self._scrub_sink.start()
            self._scrub_sink_sr = sr
            return self._scrub_io
        except Exception:
            self._scrub_sink = None
            self._scrub_io = None
            return None

    def play_scrub_grain(self, cs: float) -> None:
        """Play a smooth 65 ms windowed PCM audio grain at `cs` while dragging the playhead."""
        if not self.scrub_audio_enabled or self.is_playing:
            return
        now = time.perf_counter()
        if (now - self._last_scrub_grain_wall) < 0.045:
            return
        wf = self.active_waveform
        if wf is None or wf.scrub_pcm is None or len(wf.scrub_pcm) == 0:
            return
        io = self._ensure_scrub_sink(wf.scrub_sr)
        if io is None:
            return

        self._last_scrub_grain_wall = now
        sr = wf.scrub_sr
        grain_len = max(64, int(sr * 0.065))  # 65 ms grain
        start_idx = max(0, min(len(wf.scrub_pcm) - grain_len, int((cs / 100.0) * sr)))
        chunk = wf.scrub_pcm[start_idx : start_idx + grain_len].astype(np.float32)
        if len(chunk) < 16:
            return

        # Apply smooth 8 ms cosine fade-in/out to prevent clicks/pops
        fade = min(len(chunk) // 4, max(8, int(sr * 0.008)))
        ramp = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, fade, dtype=np.float32)))
        chunk[:fade] *= ramp
        chunk[-fade:] *= ramp[::-1]
        pcm_bytes = np.clip(chunk, -32768.0, 32767.0).astype(np.int16).tobytes()
        try:
            io.write(pcm_bytes)
        except Exception:
            pass

    def set_position_cs(self, cs: float, *, scrub_audio: bool = False) -> None:
        cs = max(0.0, float(cs))
        pos_ms = cs * 10.0
        target_int = int(round(pos_ms))
        self._reanchor(pos_ms)
        self._seek_target_ms = pos_ms
        self._seek_guard_until = time.perf_counter() + 0.22

        if self.is_playing and not self._player.source().isEmpty():
            self._awaiting_audio_start = True
            self._play_start_wall = time.perf_counter()
            self._play_start_pos_ms = pos_ms

        # Debounce rapid drag seeks to QMediaPlayer so async Windows seeks never pile up
        self._pending_seek_ms = target_int
        if not self._seek_debounce_timer.isActive():
            self._seek_debounce_timer.start()

        if scrub_audio:
            self.play_scrub_grain(cs)

        self.position_changed_float.emit(cs)
        self.position_changed.emit(int(round(cs)))

    def set_playback_rate(self, rate: float) -> None:
        # Preserve current interpolated position before changing rate
        cur_ms = self.smooth_position_cs * 10.0
        self._rate = max(0.1, float(rate))
        self._reanchor(cur_ms)
        self._player.setPlaybackRate(self._rate)

    def set_volume(self, vol: float) -> None:
        v = max(0.0, min(1.0, vol))
        self._output.setVolume(v)
        if self._scrub_sink is not None:
            self._scrub_sink.setVolume(v)

    def play(self) -> None:
        self._stop_scrub_sink()
        self._stop_at_cs = None
        cur_ms = self._smooth_cs * 10.0
        # Ensure any debounced scrub position is committed to QMediaPlayer before play starts
        self._seek_debounce_timer.stop()
        self._pending_seek_ms = None
        target_int = int(round(cur_ms))
        self._seek_target_ms = cur_ms
        self._seek_guard_until = time.perf_counter() + 0.22
        if not self._player.source().isEmpty():
            self._player.setPosition(target_int)
        self._reanchor(cur_ms)
        self._awaiting_audio_start = not self._player.source().isEmpty()
        self._play_start_wall = time.perf_counter()
        self._play_start_pos_ms = cur_ms
        self._player.play()
        self._poll_timer.start()
        if self._player.source().isEmpty():
            self.state_changed.emit(True)

    def pause(self) -> None:
        was_silent_playing = self._player.source().isEmpty() and self._poll_timer.isActive()
        cur_ms = self.smooth_position_cs * 10.0
        self._stop_at_cs = None
        self._awaiting_audio_start = False
        self._player.pause()
        self._poll_timer.stop()
        self._reanchor(cur_ms)
        if was_silent_playing:
            self.state_changed.emit(False)

    def toggle_play(self) -> None:
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def play_range(self, start_cs: int, end_cs: int) -> None:
        self._stop_scrub_sink()
        self._seek_debounce_timer.stop()
        self._pending_seek_ms = None
        self._stop_at_cs = float(max(start_cs + 1, end_cs))
        pos_ms = float(max(0, start_cs) * 10)
        self._seek_target_ms = pos_ms
        self._seek_guard_until = time.perf_counter() + 0.22
        self._player.setPosition(int(round(pos_ms)))
        self._reanchor(pos_ms)
        self._awaiting_audio_start = not self._player.source().isEmpty()
        self._play_start_wall = time.perf_counter()
        self._play_start_pos_ms = pos_ms
        self._player.play()
        self._poll_timer.start()

    def _on_poll(self) -> None:
        now = time.perf_counter()
        dt = max(0.0, min(0.1, now - self._last_tick_wall))
        self._last_tick_wall = now

        if self._awaiting_audio_start:
            # Hold position while Windows audio backend warms up (avoids start jump-back)
            if (now - self._play_start_wall) < 0.35:
                return
            self._awaiting_audio_start = False
            self._backend_wall = now

        # Phase-locked smooth advance: strictly forward, never jumps backwards on backend jitter
        est_backend_ms = self._backend_pos_ms + (now - self._backend_wall) * 1000.0 * self._rate
        err_ms = est_backend_ms - self._clock_ms
        if abs(err_ms) > 300.0:
            self._clock_ms = max(0.0, est_backend_ms)
        else:
            slew = 1.0 + max(-0.08, min(0.08, err_ms / 400.0))
            self._clock_ms = max(0.0, self._clock_ms + dt * 1000.0 * self._rate * slew)

        cs = self._clock_ms / 10.0
        if self._stop_at_cs is not None and cs >= self._stop_at_cs:
            stop_cs = self._stop_at_cs
            self._clock_ms = stop_cs * 10.0
            self._smooth_cs = stop_cs
            self.position_changed_float.emit(stop_cs)
            self.position_changed.emit(int(round(stop_cs)))
            self.pause()
            return
        self._smooth_cs = cs
        self.position_changed_float.emit(cs)
        self.position_changed.emit(int(round(cs)))

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        if playing:
            self._last_tick_wall = time.perf_counter()
            if not self._poll_timer.isActive():
                self._poll_timer.start()
        else:
            self._awaiting_audio_start = False
            self._poll_timer.stop()
        self.state_changed.emit(playing)
