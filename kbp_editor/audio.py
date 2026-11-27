from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
from PySide6.QtCore import QIODevice, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioOutput, QAudioSink, QMediaDevices, QMediaPlayer


class _PcmStreamDevice(QIODevice):
    """Always-open sequential PCM stream so QAudioSink can suspend/resume in <0.3 ms without reopening WASAPI."""

    def __init__(self, data: bytes, sr: int, channels: int, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._data: bytes = data
        self._mv = memoryview(data)
        self.sr: int = max(1, sr)
        self.channels: int = max(1, channels)
        self.frame_bytes: int = self.channels * 2
        self.pos_bytes: int = 0
        self.eof: bool = False
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    @property
    def duration_ms(self) -> float:
        total_frames = len(self._mv) // self.frame_bytes
        return (total_frames / self.sr) * 1000.0

    def set_data(self, data: bytes, sr: int, channels: int) -> None:
        self._data = data
        self._mv = memoryview(data)
        self.sr = max(1, sr)
        self.channels = max(1, channels)
        self.frame_bytes = self.channels * 2
        self.pos_bytes = min(self.pos_bytes, len(self._mv))
        self.eof = False

    def seek_ms(self, pos_ms: float) -> float:
        total_frames = len(self._mv) // self.frame_bytes
        frame = max(0, min(total_frames, int(round((pos_ms / 1000.0) * self.sr))))
        self.pos_bytes = frame * self.frame_bytes
        self.eof = frame >= total_frames
        return (frame / self.sr) * 1000.0

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        # Always report data available so QAudioSink stays active/suspended and never needs a slow reopen
        return max(32768, len(self._mv) - self.pos_bytes) + super().bytesAvailable()

    def readData(self, maxlen: int) -> bytes:
        if maxlen <= 0:
            return b""
        # Keep frame alignment
        req = (maxlen // self.frame_bytes) * self.frame_bytes
        if req <= 0:
            return b""
        rem = len(self._mv) - self.pos_bytes
        if rem <= 0:
            self.eof = True
            return bytes(req)
        n = min(req, rem)
        chunk = self._mv[self.pos_bytes : self.pos_bytes + n].tobytes()
        self.pos_bytes += n
        if n < req:
            self.eof = True
            chunk += bytes(req - n)
        return chunk

    def writeData(self, data) -> int:
        return -1


class WaveformData:
    """Precomputed peak envelope + compact mono int16 PCM buffer for low-latency scrubbing & playback."""

    def __init__(
        self,
        peaks: np.ndarray,
        duration_cs: int,
        buckets_per_sec: int = 200,
        scrub_pcm: Optional[np.ndarray] = None,
        scrub_sr: int = 22050,
        play_pcm: Optional[bytes] = None,
        play_sr: int = 44100,
        play_channels: int = 2,
    ) -> None:
        self.peaks = peaks  # float32 array in [0.0, 1.0]
        self.duration_cs = duration_cs
        self.buckets_per_sec = buckets_per_sec
        self.scrub_pcm = scrub_pcm  # optional 1D int16 mono PCM for instant scrubbing
        self.scrub_sr = scrub_sr
        self.play_pcm = play_pcm  # interleaved int16 PCM for instant low-latency QAudioSink playback
        self.play_sr = play_sr
        self.play_channels = play_channels

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

        # Full-quality int16 PCM buffer for instant-start, sample-accurate 1.0x QAudioSink playback
        play_pcm: Optional[bytes] = None
        play_channels = min(2, data.shape[1]) if data.ndim == 2 else 1
        if duration_sec <= 1200.0 and total_samples > 0 and sr > 0:
            play_arr = data[:, :play_channels] if data.ndim == 2 else data
            play_pcm = np.ascontiguousarray(
                np.clip(play_arr * 32767.0, -32768.0, 32767.0).astype(np.int16)
            ).tobytes()

        return cls(
            peaks=peaks.astype(np.float32),
            duration_cs=duration_cs,
            buckets_per_sec=buckets_per_sec,
            scrub_pcm=scrub_pcm,
            scrub_sr=int(scrub_sr),
            play_pcm=play_pcm,
            play_sr=int(sr),
            play_channels=int(play_channels),
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

        # Persistent pre-warmed PCM sink (1.0x via QAudioSink suspend/resume -> <0.3 ms play/pause)
        self._pcm_sink: Optional[QAudioSink] = None
        self._pcm_stream: Optional[_PcmStreamDevice] = None
        self._pcm_sink_sr: int = 0
        self._pcm_sink_ch: int = 0
        self._pcm_active: bool = False
        self._pcm_start_ms: float = 0.0
        self._pcm_base_processed_us: int = 0
        self._pcm_hw_pos_ms: float = 0.0
        self._pcm_hw_wall: float = 0.0
        self._pcm_duration_ms: float = 0.0

        # Low-latency scrub audio output
        self.scrub_audio_enabled: bool = False
        self._scrub_sink: Optional[QAudioSink] = None
        self._scrub_io: Optional[QIODevice] = None
        self._scrub_sink_sr: int = 0
        self._last_scrub_grain_wall: float = 0.0

        self._stop_at_cs: Optional[float] = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._poll_timer.setInterval(8)  # ~120 fps for crisp playhead tracking
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
            if self.vocal_waveform is None:
                self._teardown_pcm()
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
        if self.main_waveform is None:
            self._teardown_pcm()
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
            self._ensure_pcm_sink(wf)
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
            self._ensure_pcm_sink(wf)
        self.waveform_loaded.emit()
        return True

    def set_use_vocal_audio(self, enabled: bool) -> None:
        pos_ms = self.smooth_position_cs * 10.0
        was_playing = self.is_playing
        if enabled and self.vocal_path and self.vocal_path.exists():
            self.use_vocal_audio = True
            self._player.setSource(QUrl.fromLocalFile(str(self.vocal_path)))
        elif not enabled and self.main_path and self.main_path.exists():
            self.use_vocal_audio = False
            self._player.setSource(QUrl.fromLocalFile(str(self.main_path)))
        else:
            return
        self._reanchor(pos_ms)
        if was_playing:
            if self._can_use_pcm() and self._start_pcm_at(pos_ms):
                self._player.pause()
                if not self._poll_timer.isActive():
                    self._poll_timer.start()
            else:
                self._stop_pcm()
                self._player.setPosition(int(round(pos_ms)))
                self._player.play()

    @property
    def active_waveform(self) -> Optional[WaveformData]:
        if self.vocal_waveform is not None and self.use_vocal_audio:
            return self.vocal_waveform
        return self.main_waveform or self.vocal_waveform

    @property
    def is_playing(self) -> bool:
        return (
            self._pcm_active
            or self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
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

    def _can_use_pcm(self) -> bool:
        wf = self.active_waveform
        return (
            abs(self._rate - 1.0) < 1e-3
            and wf is not None
            and wf.play_pcm is not None
            and wf.play_sr > 0
            and wf.play_channels > 0
        )

    def _teardown_pcm(self) -> None:
        self._pcm_active = False
        if self._pcm_sink is not None:
            try:
                self._pcm_sink.stop()
            except Exception:
                pass
            self._pcm_sink.deleteLater()
            self._pcm_sink = None
        if self._pcm_stream is not None:
            try:
                self._pcm_stream.close()
            except Exception:
                pass
            self._pcm_stream.deleteLater()
            self._pcm_stream = None
        self._pcm_sink_sr = 0
        self._pcm_sink_ch = 0

    def _stop_pcm(self) -> None:
        """Suspend the pre-warmed QAudioSink in ~0.2 ms without closing the WASAPI device."""
        self._pcm_active = False
        if self._pcm_sink is not None:
            try:
                self._pcm_sink.suspend()
            except Exception:
                pass

    def _ensure_pcm_sink(self, wf: Optional[WaveformData]) -> bool:
        """Open & suspend a persistent QAudioSink once per audio format so Play/Pause/Seek take <0.3 ms."""
        if wf is None or wf.play_pcm is None or wf.play_sr <= 0 or wf.play_channels <= 0:
            return False
        if (
            self._pcm_sink is not None
            and self._pcm_stream is not None
            and self._pcm_sink_sr == wf.play_sr
            and self._pcm_sink_ch == wf.play_channels
        ):
            if self._pcm_stream._data is not wf.play_pcm:
                self._pcm_stream.set_data(wf.play_pcm, wf.play_sr, wf.play_channels)
            return True

        self._teardown_pcm()
        try:
            dev = QMediaDevices.defaultAudioOutput()
            if dev.isNull():
                return False
            fmt = QAudioFormat()
            fmt.setSampleRate(wf.play_sr)
            fmt.setChannelCount(wf.play_channels)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)

            stream = _PcmStreamDevice(wf.play_pcm, wf.play_sr, wf.play_channels, self)
            sink = QAudioSink(dev, fmt, self)
            sink.setVolume(self._output.volume())
            # 25 ms hardware buffer for instantaneous start/stop without underruns
            sink.setBufferSize(max(2048, int(wf.play_sr * wf.play_channels * 2 * 0.025)))
            sink.start(stream)
            sink.suspend()

            self._pcm_stream = stream
            self._pcm_sink = sink
            self._pcm_sink_sr = wf.play_sr
            self._pcm_sink_ch = wf.play_channels
            self._pcm_active = False
            return True
        except Exception:
            self._teardown_pcm()
            return False

    def _start_pcm_at(self, pos_ms: float) -> bool:
        wf = self.active_waveform
        if not self._ensure_pcm_sink(wf) or self._pcm_sink is None or self._pcm_stream is None:
            return False
        try:
            actual_start_ms = self._pcm_stream.seek_ms(pos_ms)
            self._pcm_sink.setVolume(self._output.volume())
            self._pcm_base_processed_us = int(self._pcm_sink.processedUSecs())
            if not self._pcm_active:
                self._pcm_sink.resume()

            now = time.perf_counter()
            self._pcm_active = True
            self._pcm_start_ms = actual_start_ms
            self._pcm_hw_pos_ms = actual_start_ms
            self._pcm_hw_wall = now
            self._pcm_duration_ms = self._pcm_stream.duration_ms
            self._reanchor(actual_start_ms)
            self._awaiting_audio_start = False
            return True
        except Exception:
            self._teardown_pcm()
            return False

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

        if self._pcm_active:
            # Instant zero-debounce seek in direct PCM mode
            self._seek_debounce_timer.stop()
            self._pending_seek_ms = None
            if not self._start_pcm_at(pos_ms):
                self._stop_pcm()
                self._player.setPosition(target_int)
                self._player.play()
        elif self._can_use_pcm() and not self.is_playing:
            # Paused in PCM-capable mode: no need to spam QMediaPlayer while scrubbing
            self._seek_debounce_timer.stop()
            self._pending_seek_ms = None
        else:
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
        was_playing = self.is_playing
        self._rate = max(0.1, float(rate))
        self._reanchor(cur_ms)
        self._player.setPlaybackRate(self._rate)
        if was_playing:
            if self._can_use_pcm():
                if not self._pcm_active and self._start_pcm_at(cur_ms):
                    self._player.pause()
                    if not self._poll_timer.isActive():
                        self._poll_timer.start()
            elif self._pcm_active:
                self._stop_pcm()
                if not self._player.source().isEmpty():
                    self._player.setPosition(int(round(cur_ms)))
                    self._awaiting_audio_start = True
                    self._play_start_wall = time.perf_counter()
                    self._play_start_pos_ms = cur_ms
                    self._player.play()

    def set_volume(self, vol: float) -> None:
        v = max(0.0, min(1.0, vol))
        self._output.setVolume(v)
        if self._scrub_sink is not None:
            self._scrub_sink.setVolume(v)
        if self._pcm_sink is not None:
            self._pcm_sink.setVolume(v)

    def play(self) -> None:
        self._stop_scrub_sink()
        self._stop_at_cs = None
        cur_ms = self._smooth_cs * 10.0
        # Ensure any debounced scrub position is committed before play starts
        self._seek_debounce_timer.stop()
        self._pending_seek_ms = None
        if self._can_use_pcm() and self._start_pcm_at(cur_ms):
            self._player.pause()
            self._poll_timer.start()
            self.state_changed.emit(True)
            return
        self._stop_pcm()
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
        was_pcm = self._pcm_active
        was_silent_playing = (
            not was_pcm
            and self._player.source().isEmpty()
            and self._poll_timer.isActive()
        )
        cur_ms = self.smooth_position_cs * 10.0
        self._stop_at_cs = None
        self._awaiting_audio_start = False
        if was_pcm:
            self._stop_pcm()
        self._player.pause()
        self._poll_timer.stop()
        self._reanchor(cur_ms)
        if was_pcm or was_silent_playing:
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
        if self._can_use_pcm() and self._start_pcm_at(pos_ms):
            self._player.pause()
            self._poll_timer.start()
            self.state_changed.emit(True)
            return
        self._stop_pcm()
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

        if self._pcm_active and self._pcm_sink is not None:
            delta_ms = max(0.0, (self._pcm_sink.processedUSecs() - self._pcm_base_processed_us) / 1000.0)
            hw_ms = self._pcm_start_ms + delta_ms
            if hw_ms > self._pcm_hw_pos_ms + 0.05:
                self._pcm_hw_pos_ms = hw_ms
                self._pcm_hw_wall = now
            # Smoothly interpolate between ~10 ms hardware audio buffer ticks
            interp_ms = self._pcm_hw_pos_ms + min(25.0, (now - self._pcm_hw_wall) * 1000.0)
            self._clock_ms = max(self._clock_ms, min(self._pcm_duration_ms, interp_ms))
            cs = self._clock_ms / 10.0

            if self._stop_at_cs is not None and cs >= self._stop_at_cs:
                stop_cs = self._stop_at_cs
                self._clock_ms = stop_cs * 10.0
                self._smooth_cs = stop_cs
                self.position_changed_float.emit(stop_cs)
                self.position_changed.emit(int(round(stop_cs)))
                self.pause()
                return

            if (
                self._pcm_stream is not None
                and self._pcm_stream.eof
                and hw_ms >= self._pcm_duration_ms - 15.0
            ):
                end_cs = self._pcm_duration_ms / 10.0
                self._clock_ms = self._pcm_duration_ms
                self._smooth_cs = end_cs
                self.position_changed_float.emit(end_cs)
                self.position_changed.emit(int(round(end_cs)))
                self.pause()
                return

            self._smooth_cs = cs
            self.position_changed_float.emit(cs)
            self.position_changed.emit(int(round(cs)))
            return

        if self._awaiting_audio_start:
            # Brief hold while QMediaPlayer backend warms up (50%/75% fallback)
            if (now - self._play_start_wall) < 0.12:
                return
            self._awaiting_audio_start = False
            self._backend_wall = now

        # Phase-locked smooth advance: strictly forward, never jumps backwards on backend jitter
        est_backend_ms = self._backend_pos_ms + (now - self._backend_wall) * 1000.0 * self._rate
        err_ms = est_backend_ms - self._clock_ms
        if abs(err_ms) > 120.0:
            self._clock_ms = max(0.0, est_backend_ms)
        else:
            slew = 1.0 + max(-0.25, min(0.25, err_ms / 120.0))
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
        if self._pcm_active:
            return
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        if playing:
            self._last_tick_wall = time.perf_counter()
            if not self._poll_timer.isActive():
                self._poll_timer.start()
        else:
            self._awaiting_audio_start = False
            self._poll_timer.stop()
        self.state_changed.emit(playing)
