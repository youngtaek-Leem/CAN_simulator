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

    def start(self, filename: str) -> dict:
        if not self.initialized:
            return {"ok": False, "reason": "오디오 장치를 찾을 수 없습니다"}
        if self.device_index is None:
            return {"ok": False, "reason": "오디오 장치가 선택되지 않았습니다"}
        try:
            samplerate = int(sd.query_devices(self.device_index)["default_samplerate"])
            self._audio_data = []
            self._is_recording = True
            self._wav_name = filename

            def callback(indata, _frames, _time, _status):
                if self._is_recording:
                    self._audio_data.append(indata.copy())

            self._stream = sd.InputStream(
                samplerate=samplerate,
                device=self.device_index,
                channels=len(DEFAULT_CHANNELS),
                dtype="int16",
                callback=callback,
            )
            self._stream.start()
            return {"ok": True, "filename": filename}
        except Exception as exc:
            self._is_recording = False
            return {"ok": False, "reason": str(exc)}

    def stop(self) -> dict:
        if not self._is_recording:
            return {"ok": False, "reason": "녹음 중이 아닙니다"}
        self._is_recording = False
        try:
            self._stream.stop()
            self._stream.close()
            samplerate = int(sd.query_devices(self.device_index)["default_samplerate"])
            audio = (
                np.concatenate(self._audio_data, axis=0)
                if self._audio_data
                else np.zeros((0, len(DEFAULT_CHANNELS)), dtype="int16")
            )
            path = self._rec_dir / self._wav_name
            wav_write(str(path), samplerate, audio)
            return {"ok": True, "filename": self._wav_name, "frames": int(len(audio))}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

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
