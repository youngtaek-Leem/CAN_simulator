"""Audio recording + waveform comparison for the test scenario runner.

Recording mechanics (sounddevice InputStream + callback, int16 stereo,
default samplerate of the selected device) are ported as-is from
Automation/AppTest.py's Audio class -- that recording path is already
field-validated, so it is intentionally left unchanged.

The comparison algorithm is upgraded from AppTest.py's original single
cross-correlation metric to the multi-metric approach already prototyped in
Automation/compareWAV_MFCC.py (MFCC+DTW similarity, full-spectrum FFT
correlation, voice-band FFT correlation, cross-correlation, RMS/ZCR/spectral
centroid difference), and compares each recording against a fixed per-case
"golden" reference WAV instead of AppTest.py's consecutive-cycle comparison
(cycle N vs cycle N-1), which can't catch every cycle drifting the same way
and has no stable baseline across separate test runs.
"""

import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import sounddevice as sd
    from scipy.io.wavfile import read as wav_read
    from scipy.io.wavfile import write as wav_write

    _RECORDING_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via _RECORDING_AVAILABLE branch
    _RECORDING_AVAILABLE = False

try:
    import librosa
    from librosa.sequence import dtw
    from scipy.signal import correlate
    from sklearn.metrics.pairwise import cosine_similarity

    _COMPARE_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via _COMPARE_AVAILABLE branch
    _COMPARE_AVAILABLE = False

# CH3/CH4 (0-indexed 1,2), matches AppTest.py's channel mapping for its
# specific multi-channel audio interface.
DEFAULT_CHANNELS = [1, 2]
DEFAULT_THRESHOLD = 0.8  # MFCC-DTW cosine similarity pass threshold

# 오디오 신호 모니터 위젯의 Record 기능: 긴 녹음이 파일 하나로 무한정 커지지 않도록
# 30분 단위로 잘라서 저장한다 (스트림 자체는 끊기지 않고 계속 녹음됨).
SEGMENT_DURATION_S = 30 * 60
_ROTATION_CHECK_INTERVAL_S = 5.0


def generate_monitor_filename() -> str:
    """Timestamp-based filename for a 오디오 신호 모니터 recording (or one
    30-minute segment of one) -- date+time so files sort chronologically and
    never collide within the same second."""
    return f"monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"

# "오디오 신호 모니터" widget: live level (peak/RMS) for the meter bars, plus a
# rolling raw-sample ring buffer per channel so the widget's zoomable
# waveform chart can query any [from, to] slice of recent audio (see
# waveform_slice() -- the standard "min/max per pixel column" technique
# waveform viewers use, decimated to whatever resolution the chart actually
# needs instead of shipping every sample over the wire).
RAW_BUFFER_SECONDS = 30.0


class _ChannelLevelTracker:
    """Rolling level state for one captured channel. add_chunk() runs on the
    sounddevice callback thread; snapshot()/waveform_slice()/reset() are
    called from request handler threads -- callers must hold
    AudioService._level_lock for all of them."""

    def __init__(self) -> None:
        self.peak = 0.0
        self.rms = 0.0
        # list of (epoch_seconds_at_chunk_start, float32 samples in [-1, 1])
        self._raw_chunks: deque = deque()

    def add_chunk(self, samples: np.ndarray, now: float) -> None:
        """samples: one callback's worth of one channel, float32 in [-1, 1].
        now: time.time() (epoch seconds) when this callback fired -- epoch,
        not monotonic, so it lines up directly with the frontend's Date.now()
        (this is a local-machine app; both share the same system clock)."""
        if samples.size == 0:
            return
        chunk_min = float(samples.min())
        chunk_max = float(samples.max())
        self.peak = max(abs(chunk_min), abs(chunk_max))
        self.rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))

        self._raw_chunks.append((now, samples))
        cutoff = now - RAW_BUFFER_SECONDS
        while self._raw_chunks and self._raw_chunks[0][0] < cutoff:
            self._raw_chunks.popleft()

    def waveform_slice(self, from_s: float, to_s: float, max_points: int, samplerate: int) -> list[dict]:
        """Up to max_points {t, min, max} columns (epoch seconds, [-1,1])
        covering [from_s, to_s], decimated from the raw ring buffer. Zoomed
        far out -> each column averages many samples (an envelope, like a
        DAW's overview track); zoomed in close to sample-level -> each
        column is ~1 sample, i.e. the actual waveform."""
        if to_s <= from_s or max_points <= 0 or samplerate <= 0:
            return []
        column_width = (to_s - from_s) / max_points
        col_min = [None] * max_points
        col_max = [None] * max_points

        for chunk_start, arr in self._raw_chunks:
            n = len(arr)
            if n == 0:
                continue
            chunk_end = chunk_start + n / samplerate
            if chunk_end < from_s or chunk_start > to_s:
                continue
            idx0 = max(0, int((from_s - chunk_start) * samplerate))
            idx1 = min(n, int((to_s - chunk_start) * samplerate) + 1)
            if idx0 >= idx1:
                continue
            sub = arr[idx0:idx1]
            times = chunk_start + (np.arange(idx0, idx1) / samplerate)
            col_idx = np.floor((times - from_s) / column_width).astype(np.int64)
            valid = (col_idx >= 0) & (col_idx < max_points)
            if not np.any(valid):
                continue
            col_idx = col_idx[valid]
            sub = sub[valid]
            for ci in np.unique(col_idx):
                mask = col_idx == ci
                vmin = float(sub[mask].min())
                vmax = float(sub[mask].max())
                ci = int(ci)
                col_min[ci] = vmin if col_min[ci] is None else min(col_min[ci], vmin)
                col_max[ci] = vmax if col_max[ci] is None else max(col_max[ci], vmax)

        return [
            {"t": from_s + (i + 0.5) * column_width, "min": col_min[i], "max": col_max[i]}
            for i in range(max_points)
            if col_min[i] is not None
        ]

    def snapshot(self, index: int) -> dict:
        return {
            "index": index,
            "peak": round(self.peak, 4),
            "rms": round(self.rms, 4),
        }

    def reset(self) -> None:
        self.peak = 0.0
        self.rms = 0.0
        self._raw_chunks.clear()


# ---- waveform comparison (ported from Automation/compareWAV_MFCC.py) ------


def _match_length(sig1: np.ndarray, sig2: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    min_len = min(len(sig1), len(sig2))
    if min_len < 10:
        return np.zeros(10, dtype=np.float32), np.zeros(10, dtype=np.float32)
    sig1 = np.nan_to_num(sig1[:min_len], nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
    sig2 = np.nan_to_num(sig2[:min_len], nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
    return sig1, sig2


def _mfcc_dtw_similarity(sig1: np.ndarray, sig2: np.ndarray, sr: int) -> float:
    mfcc1 = librosa.feature.mfcc(y=sig1, sr=sr, n_mfcc=13).T
    mfcc2 = librosa.feature.mfcc(y=sig2, sr=sr, n_mfcc=13).T
    _, wp = dtw(X=mfcc1, Y=mfcc2, metric="euclidean")
    vec1 = np.mean(mfcc1[wp[:, 0]], axis=0).reshape(1, -1)
    vec2 = np.mean(mfcc2[wp[:, 1]], axis=0).reshape(1, -1)
    return float(cosine_similarity(vec1, vec2)[0, 0])


def _corrcoef_safe(a: np.ndarray, b: np.ndarray) -> float:
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _fft_similarity(sig1: np.ndarray, sig2: np.ndarray) -> float:
    fft1, fft2 = _match_length(np.abs(np.fft.fft(sig1)), np.abs(np.fft.fft(sig2)))
    return _corrcoef_safe(fft1, fft2)


def _fft_band_similarity(sig1: np.ndarray, sig2: np.ndarray, sr: int, low=300, high=3400) -> float:
    freqs = np.fft.fftfreq(len(sig1), d=1.0 / sr)
    mask = (freqs >= low) & (freqs <= high)
    band1, band2 = _match_length(np.abs(np.fft.fft(sig1))[mask], np.abs(np.fft.fft(sig2))[mask])
    return _corrcoef_safe(band1, band2)


def _cross_corr_similarity(sig1: np.ndarray, sig2: np.ndarray) -> float:
    def normalize(s: np.ndarray) -> np.ndarray:
        return (s - np.mean(s)) / (np.std(s) + 1e-8)

    a, b = normalize(sig1), normalize(sig2)
    norm1, norm2 = np.linalg.norm(a), np.linalg.norm(b)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    corr = correlate(a, b, mode="valid")
    return float(np.max(corr) / (norm1 * norm2))


def _statistical_features(sig: np.ndarray, sr: int) -> tuple[float, float, float]:
    rms = float(np.sqrt(np.mean(sig**2)))
    zcr = float(np.mean(librosa.feature.zero_crossing_rate(sig)))
    centroid = float(np.mean(librosa.feature.spectral_centroid(y=sig, sr=sr)))
    return rms, zcr, centroid


def compare_channel(sig1: np.ndarray, sig2: np.ndarray, sr: int) -> dict:
    sig1, sig2 = _match_length(sig1, sig2)
    rms1, zcr1, centroid1 = _statistical_features(sig1, sr)
    rms2, zcr2, centroid2 = _statistical_features(sig2, sr)
    return {
        "mfcc_dtw_similarity": _mfcc_dtw_similarity(sig1, sig2, sr),
        "fft_similarity": _fft_similarity(sig1, sig2),
        "fft_band_similarity": _fft_band_similarity(sig1, sig2, sr),
        "cross_corr_similarity": _cross_corr_similarity(sig1, sig2),
        "rms_diff": abs(rms1 - rms2),
        "zcr_diff": abs(zcr1 - zcr2),
        "centroid_diff": abs(centroid1 - centroid2),
    }


def choose_channel_count(max_input_channels: int) -> int:
    """How many channels to request from InputStream for a device that
    reports `max_input_channels` -- capped at DEFAULT_CHANNELS' length so a
    real multi-channel interface still gets its usual 2 channels, but a
    plain 1-channel mic doesn't get asked for more than it has (PortAudio
    raises PaErrorCode -9998 "Invalid number of channels" otherwise)."""
    return min(len(DEFAULT_CHANNELS), max_input_channels)


class AudioService:
    def __init__(self, rec_dir: Path, golden_dir: Path):
        self._rec_dir = rec_dir
        self._golden_dir = golden_dir
        self.device_index: Optional[int] = None
        self.initialized = False
        self.error: Optional[str] = None
        self._devices: list[dict] = []
        self._is_recording = False
        self._audio_data: list[np.ndarray] = []
        self._stream = None
        self._wav_name: Optional[str] = None
        # "recording" (owned by the test runner's start()/stop()) or
        # "monitor" (owned by the 오디오 신호 모니터 widget's start_monitor()/
        # stop_monitor()) -- whichever opened the current stream, so the
        # other side knows not to close a stream it doesn't own. Only one
        # stream can be open on a device at a time; start() will upgrade an
        # existing monitor-only stream in place instead of opening a second
        # one (see start()).
        self._stream_owner: Optional[str] = None
        self._level_lock = threading.Lock()
        self._level_trackers = [_ChannelLevelTracker() for _ in DEFAULT_CHANNELS]
        # How many channels the currently- (or most-recently-) opened stream
        # actually captures. Starts at len(DEFAULT_CHANNELS) so the monitor
        # widget shows its usual CH1/CH2 placeholders before anything opens;
        # updated in _open_stream() to whatever the selected device actually
        # supports (see there for why).
        self._active_channels = len(DEFAULT_CHANNELS)
        self._active_samplerate: Optional[int] = None
        # Guards self._audio_data -- appended to on the sounddevice callback
        # thread, swapped out (for a 30-min segment rotation, or on stop())
        # from request-handler threads.
        self._audio_data_lock = threading.Lock()
        # epoch seconds. _stream_started_at is the anchor for the widget's
        # waveform x-axis ("0s at the moment Start/Record began") -- set for
        # *any* open stream (plain monitor or an actual recording), so Start
        # and Record behave identically; stays fixed across 30-min segment
        # rotations. _segment_started_at resets on every rotation and drives
        # the file-split timer (recording only).
        self._stream_started_at: Optional[float] = None
        self._segment_started_at: Optional[float] = None
        self._rotation_thread = threading.Thread(target=self._rotation_loop, daemon=True)
        self._rotation_thread.start()
        self.refresh_devices()

    def refresh_devices(self) -> dict:
        if not _RECORDING_AVAILABLE:
            self.error = "sounddevice가 설치되어 있지 않습니다"
            self.initialized = False
            return self.info()
        try:
            self._devices = [
                {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
                for i, d in enumerate(sd.query_devices())
            ]
            self.initialized = True
            self.error = None
        except Exception as exc:
            self.error = str(exc)
            self.initialized = False
        return self.info()

    def select_device(self, index: int) -> dict:
        self.device_index = index
        return self.info()

    def info(self) -> dict:
        return {
            "initialized": self.initialized,
            "error": self.error,
            "device_index": self.device_index,
            "devices": list(self._devices),
            "recording": self._is_recording,
        }

    # ---- recording (ported as-is from AppTest.py's start_recording/stop_recording) --

    def _make_callback(self):
        # Runs on the sounddevice callback thread. Always updates the live
        # level trackers (so the monitor widget sees data regardless of who
        # opened the stream); only buffers raw audio into _audio_data while
        # a WAV filename is set (i.e. an actual test-runner recording).
        def callback(indata, _frames, _time, _status):
            now = time.time()
            with self._level_lock:
                for i, tracker in enumerate(self._level_trackers):
                    if i < indata.shape[1]:
                        samples = indata[:, i].astype(np.float32) / 32768.0
                        tracker.add_chunk(samples, now)
            if self._wav_name is not None:
                with self._audio_data_lock:
                    self._audio_data.append(indata.copy())

        return callback

    def _rotation_loop(self) -> None:
        """Background timer: every few seconds, check whether the widget's
        current Record segment has run 30 minutes and needs to roll over to
        a new file. Runs for the lifetime of the process regardless of
        whether anything is actually recording (cheap no-op check)."""
        while True:
            time.sleep(_ROTATION_CHECK_INTERVAL_S)
            try:
                self._maybe_rotate_segment()
            except Exception:
                pass  # never let the timer thread die over one bad rotation

    def _maybe_rotate_segment(self) -> None:
        if self._stream_owner != "widget_record" or not self._is_recording:
            return
        if self._segment_started_at is None:
            return
        if time.time() - self._segment_started_at < SEGMENT_DURATION_S:
            return
        self._rotate_segment()

    def _rotate_segment(self) -> None:
        """Flush the current segment to disk under its own timestamped
        filename and start a fresh one -- the InputStream itself is never
        stopped, so the recording keeps going without a gap."""
        with self._audio_data_lock:
            audio_data = self._audio_data
            self._audio_data = []
        old_name = self._wav_name
        samplerate = self._active_samplerate
        channels = self._active_channels
        self._wav_name = generate_monitor_filename()
        self._segment_started_at = time.time()
        try:
            audio = (
                np.concatenate(audio_data, axis=0)
                if audio_data
                else np.zeros((0, channels), dtype="int16")
            )
            path = self._rec_dir / old_name
            wav_write(str(path), samplerate, audio)
        except Exception:
            pass  # best-effort -- a failed segment write must not stop the recording

    def _open_stream(self, wav_name: Optional[str], owner: str) -> dict:
        if not self.initialized:
            return {"ok": False, "reason": "오디오 장치를 찾을 수 없습니다"}
        if self.device_index is None:
            return {"ok": False, "reason": "오디오 장치가 선택되지 않았습니다"}
        try:
            device_info = sd.query_devices(self.device_index)
            samplerate = int(device_info["default_samplerate"])
            # DEFAULT_CHANNELS assumes a real multi-channel interface (CH3/
            # CH4); a plain mic (e.g. a laptop's built-in/iPhone mic used for
            # local dev) only exposes 1 input channel, and asking
            # InputStream for more than the device has raises PaErrorCode
            # -9998 "Invalid number of channels". Cap the request at
            # whatever the selected device actually supports.
            max_input_channels = int(device_info.get("max_input_channels", 0))
            if max_input_channels <= 0:
                return {"ok": False, "reason": "선택한 장치에는 입력 채널이 없습니다"}
            channel_count = choose_channel_count(max_input_channels)

            self._audio_data = []
            self._wav_name = wav_name
            self._is_recording = wav_name is not None
            with self._level_lock:
                if channel_count != len(self._level_trackers):
                    self._level_trackers = [_ChannelLevelTracker() for _ in range(channel_count)]
                else:
                    for tracker in self._level_trackers:
                        tracker.reset()

            self._stream = sd.InputStream(
                samplerate=samplerate,
                device=self.device_index,
                channels=channel_count,
                dtype="int16",
                callback=self._make_callback(),
            )
            self._stream.start()
            self._stream_owner = owner
            self._active_channels = channel_count
            self._active_samplerate = samplerate
            self._stream_started_at = time.time()
            return {"ok": True}
        except Exception as exc:
            self._stream = None
            self._stream_owner = None
            self._is_recording = False
            self._wav_name = None
            return {"ok": False, "reason": str(exc)}

    def start(self, filename: str) -> dict:
        """Start a test-runner recording. If the 오디오 신호 모니터 widget
        already has a monitor-only stream open, upgrade it in place (same
        device stream, now also buffering to a WAV) instead of opening a
        second stream on the same device, which would fail."""
        if self._stream is not None and self._stream_owner == "monitor":
            with self._audio_data_lock:
                self._audio_data = []
            self._wav_name = filename
            self._stream_owner = "recording"
            self._is_recording = True
            self._stream_started_at = time.time()
            return {"ok": True, "filename": filename}
        if self._stream is not None:
            return {"ok": False, "reason": "이미 실행 중입니다"}
        result = self._open_stream(filename, "recording")
        if not result["ok"]:
            return result
        return {"ok": True, "filename": filename}

    def stop(self) -> dict:
        if not self._is_recording:
            return {"ok": False, "reason": "녹음 중이 아닙니다"}
        try:
            self._stream.stop()
            self._stream.close()
            samplerate = self._active_samplerate or int(sd.query_devices(self.device_index)["default_samplerate"])
            with self._audio_data_lock:
                audio_data = self._audio_data
                self._audio_data = []
            audio = (
                np.concatenate(audio_data, axis=0)
                if audio_data
                else np.zeros((0, self._active_channels), dtype="int16")
            )
            path = self._rec_dir / self._wav_name
            wav_write(str(path), samplerate, audio)
            result = {"ok": True, "filename": self._wav_name, "frames": int(len(audio))}
        except Exception as exc:
            result = {"ok": False, "reason": str(exc)}
        finally:
            self._stream = None
            self._stream_owner = None
            self._is_recording = False
            self._wav_name = None
            self._stream_started_at = None
            self._segment_started_at = None
        return result

    # ---- 오디오 신호 모니터 (live level monitor) --------------------------------

    def start_monitor(self) -> dict:
        """Start (or piggyback on an already-open) live level stream for the
        모니터 widget. Never opens a second stream if a test-runner recording
        is already active -- the level trackers are already being fed by it."""
        if self._stream is not None:
            return {"ok": True, "already_active": True}
        return self._open_stream(None, "monitor")

    def stop_monitor(self) -> dict:
        """Stop the monitor-only stream. A no-op (not an error) if nothing
        is open, and refuses if the current stream belongs to any in-progress
        recording (test-runner or the widget's own Record) -- the plain
        Stop-the-waveform-only path must never cut a recording off before it
        saves; use stop_widget_recording() for that."""
        if self._stream is None:
            return {"ok": True}
        if self._stream_owner in ("recording", "widget_record"):
            return {"ok": False, "reason": "녹음이 진행 중이라 모니터를 끌 수 없습니다"}
        try:
            self._stream.stop()
            self._stream.close()
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}
        finally:
            self._stream = None
            self._stream_owner = None
            self._stream_started_at = None
        return {"ok": True}

    def start_widget_recording(self, filename: str) -> dict:
        """오디오 신호 모니터 위젯의 Record 버튼. 이미 위젯이 연 모니터 스트림이
        있으면 그 자리에서 녹음으로 업그레이드(재오픈 없이); 테스트 러너 녹음 등
        다른 소유자의 스트림이 이미 열려 있으면 거부."""
        if self._stream is not None and self._stream_owner == "monitor":
            with self._audio_data_lock:
                self._audio_data = []
            self._wav_name = filename
            self._stream_owner = "widget_record"
            self._is_recording = True
            now = time.time()
            self._stream_started_at = now
            self._segment_started_at = now
            return {"ok": True, "filename": filename}
        if self._stream is not None:
            return {"ok": False, "reason": "이미 다른 프로세스가 오디오 스트림을 사용 중입니다"}
        result = self._open_stream(filename, "widget_record")
        if not result["ok"]:
            return result
        self._segment_started_at = self._stream_started_at
        return {"ok": True, "filename": filename}

    def stop_widget_recording(self) -> dict:
        """위젯의 Record 중지 -- 위젯이 직접 시작한 녹음일 때만 저장하고 닫는다
        (테스트 러너의 녹음은 절대 건드리지 않는다)."""
        if self._stream_owner != "widget_record":
            return {"ok": False, "reason": "위젯이 시작한 녹음이 아닙니다"}
        return self.stop()

    def get_level(self) -> dict:
        with self._level_lock:
            channels = [t.snapshot(i) for i, t in enumerate(self._level_trackers)]
        return {
            "active": self._stream is not None,
            "recording": self._is_recording,
            "monitoring": self._stream_owner == "monitor",
            "owner": self._stream_owner,
            "channels": channels,
            # epoch seconds the current stream (Start OR Record) began, fixed
            # across 30-min recording-segment rotations, or None when idle --
            # the widget's waveform x-axis uses this as its "0s" reference,
            # identically for Start and Record.
            "stream_started_at": self._stream_started_at,
            # current segment's filename, so the widget can notice a
            # rotation happened (filename changed while still recording).
            "current_filename": self._wav_name,
        }

    def get_waveform(self, from_ms: float, to_ms: float, max_points: int) -> dict:
        """Decimated waveform slice for the widget's zoomable chart.
        from_ms/to_ms: epoch milliseconds (i.e. JS Date.now() units) -- the
        frontend's chart view state is expressed directly in this unit, no
        separate clock-sync step needed since frontend and backend share the
        same machine clock here."""
        samplerate = self._active_samplerate
        with self._level_lock:
            if samplerate is None or self._stream is None:
                channels = [{"index": i, "points": []} for i in range(len(self._level_trackers))]
            else:
                channels = [
                    {"index": i, "points": t.waveform_slice(from_ms / 1000.0, to_ms / 1000.0, max_points, samplerate)}
                    for i, t in enumerate(self._level_trackers)
                ]
        return {
            "active": self._stream is not None,
            "samplerate": samplerate,
            "channels": channels,
        }

    # ---- comparison -------------------------------------------------------------

    def compare(self, filename: str, golden_name: str, threshold: float = DEFAULT_THRESHOLD) -> dict:
        if not _COMPARE_AVAILABLE:
            return {"ok": False, "reason": "librosa/scikit-learn이 설치되어 있지 않습니다"}
        rec_path = self._rec_dir / filename
        golden_path = self._golden_dir / golden_name
        if not rec_path.exists():
            return {"ok": False, "reason": f"녹음 파일 없음: {filename}"}
        if not golden_path.exists():
            return {"ok": False, "reason": f"기준(golden) 파일 없음: {golden_name}"}
        try:
            rate1, golden_data = wav_read(str(golden_path))
            _rate2, rec_data = wav_read(str(rec_path))
            n_channels = min(
                golden_data.shape[1] if golden_data.ndim > 1 else 1,
                rec_data.shape[1] if rec_data.ndim > 1 else 1,
            )
            channels = {}
            passed = True
            for ch in range(n_channels):
                sig1 = golden_data[:, ch] if golden_data.ndim > 1 else golden_data
                sig2 = rec_data[:, ch] if rec_data.ndim > 1 else rec_data
                metrics = compare_channel(sig1, sig2, rate1)
                channels[f"ch{ch}"] = metrics
                if metrics["mfcc_dtw_similarity"] < threshold:
                    passed = False
            return {"ok": passed, "threshold": threshold, "channels": channels}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    def save_as_golden(self, filename: str, golden_name: str) -> dict:
        """Promote a just-recorded WAV to be the fixed reference for a case
        (e.g. on the first-ever run of a case, or after a manual review)."""
        src = self._rec_dir / filename
        if not src.exists():
            return {"ok": False, "reason": f"녹음 파일 없음: {filename}"}
        dest = self._golden_dir / golden_name
        dest.write_bytes(src.read_bytes())
        return {"ok": True, "saved": dest.name}
