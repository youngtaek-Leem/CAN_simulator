"""오디오 파형비교 백엔드: 2개 WAV 보관 + 시작점 매칭 + 구간별 종합 비교.

- A/B 슬롯에 .wav를 업로드해 서버에 보관한다 (syslog/canlog 세션 패턴).
- 시작점 매칭: 정규화 상호상관 full-lag 탐색으로 B의 지연(offset)을 검출한다.
- 구간별 비교: 정렬된 신호를 segment_ms(기본 100ms) 윈도우로 나눠 구간마다
  Amplitude(RMS 상대 유사도) + Shape(MFCC-DTW, 상호상관) + 주파수(전체/대역
  FFT 상관, 스펙트럴 센트로이드, ZCR) 7개 지표를 0~1 유사도로 정규화해
  종합 점수(평균)로 판정한다. 개별 지표 단독 비교도 선택 가능하다.
- 서로 다른 샘플레이트 파일은 librosa.resample으로 통일한다
  (audio_service._compare_wav_files는 두 번째 sr을 무시하므로 여기서 해결).
- 테스트 sequence 실행기와 알고리즘 공유: 실행기는 경로 기반 compare_files()
  (녹음/골든 파일 직접 지정, channel="all" 전채널 AND)를 호출하고, 비교한
  2파일을 슬롯에 게시 + _last_result 저장해 파형비교 위젯에 표시한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

import audio_service
from audio_service import (
    DEFAULT_THRESHOLD,
    _corrcoef_safe,
    _cross_corr_similarity,
    _fft_band_similarity,
    _fft_similarity,
    _match_length,
    _statistical_features,
)

ALGORITHMS = (
    "comprehensive",
    "mfcc_dtw",
    "fft",
    "fft_band",
    "xcorr",
    "rms",
    "zcr",
    "centroid",
)

# 개별 지표의 compare_channel 키 -> 유사도(0~1) 변환. diff 계열은 상대 유사도로 변환한다.
SIMILARITY_KEYS = ("mfcc_dtw_similarity", "fft_similarity", "fft_band_similarity", "cross_corr_similarity")
ALGORITHM_KEY = {
    "mfcc_dtw": "mfcc_dtw_similarity",
    "fft": "fft_similarity",
    "fft_band": "fft_band_similarity",
    "xcorr": "cross_corr_similarity",
}

DEFAULT_SEGMENT_MS = 100
DEFAULT_PARAMS = {
    "algorithm": "comprehensive",
    "threshold": DEFAULT_THRESHOLD,
    "segment_ms": DEFAULT_SEGMENT_MS,
    "align": True,
}
# 시작점 탐색용 다운샘플 상한 (이 샘플 수 이하가 되도록 decimation)
_ALIGN_MAX_SAMPLES = 50_000


def _rel_similarity(v1: float, v2: float) -> float:
    """상대 유사도 0~1: 같으면 1, 한쪽이 0이면 0에 수렴."""
    denom = max(abs(v1), abs(v2), 1e-9)
    return float(max(0.0, 1.0 - abs(v1 - v2) / denom))


def _mfcc_dtw_similarity_fixed(sig1: np.ndarray, sig2: np.ndarray, sr: int) -> float:
    """MFCC+DTW 유사도 -- audio_service._mfcc_dtw_similarity와 같은 아이디어지만
    librosa dtw 규격(K, N)=(특징, 프레임) 방향으로 호출한다.

    audio_service 쪽은 (프레임, 13) 방향으로 넘겨 warp 경로 인덱스가 MFCC
    계수 개수(13) 범위로만 나와서, 프레임이 13개 미만인 짧은 구간(예: 100ms)
    에서는 IndexError가 난다. 전체 신호처럼 프레임이 많으면 우연히 동작하므로
    기존 동작은 그대로 두고, 이 서비스의 구간 비교에서는 올바른 방향을 쓴다.
    """
    librosa = audio_service.librosa
    dtw = audio_service.dtw
    cosine_similarity = audio_service.cosine_similarity
    m1 = librosa.feature.mfcc(y=np.asarray(sig1, dtype=np.float32), sr=sr, n_mfcc=13)
    m2 = librosa.feature.mfcc(y=np.asarray(sig2, dtype=np.float32), sr=sr, n_mfcc=13)
    _, wp = dtw(X=m1, Y=m2, metric="euclidean")
    vec1 = np.mean(m1[:, wp[:, 0]], axis=1).reshape(1, -1)
    vec2 = np.mean(m2[:, wp[:, 1]], axis=1).reshape(1, -1)
    return float(cosine_similarity(vec1, vec2)[0, 0])


def _segment_sims(sa: np.ndarray, sb: np.ndarray, sr: int) -> dict[str, float]:
    """한 구간(짧은 신호 ok)의 7종 유사도. compare_channel과 같은 지표 구성이지만
    MFCC-DTW만 올바른 축 방향으로 계산한다."""
    sa, sb = _match_length(sa, sb)
    rms_a, zcr_a, cent_a = _statistical_features(sa, sr)
    rms_b, zcr_b, cent_b = _statistical_features(sb, sr)

    def clip(x: float) -> float:
        return float(min(1.0, max(0.0, x)))

    return {
        "mfcc_dtw": clip(_mfcc_dtw_similarity_fixed(sa, sb, sr)),
        "fft": clip(_fft_similarity(sa, sb)),
        "fft_band": clip(_fft_band_similarity(sa, sb, sr)),
        "xcorr": clip(_cross_corr_similarity(sa, sb)),
        "rms": _rel_similarity(rms_a, rms_b),
        "zcr": _rel_similarity(zcr_a, zcr_b),
        "centroid": _rel_similarity(cent_a, cent_b),
    }


def _segment_pass(algorithm: str, sims: dict[str, float], threshold: float) -> tuple[bool, float]:
    """(통과 여부, 대표값). comprehensive는 7개 유사도 평균과 임계값 비교."""
    if algorithm == "comprehensive":
        score = float(sum(sims.values()) / len(sims))
        return score >= threshold, score
    value = sims[algorithm]
    return value >= threshold, value


def _find_offset_samples(a: np.ndarray, b: np.ndarray) -> int:
    """B가 A보다 몇 샘플 늦게 시작하는지 전역 탐색 (양수=B 지연).

    연산량 제한을 위해 최대 _ALIGN_MAX_SAMPLES까지 decimation 후 full-mode
    상호상관 피크로 lag를 찾고 원래 스케일로 환산한다.
    """
    from scipy.signal import correlate

    factor = max(1, int(max(len(a), len(b)) / _ALIGN_MAX_SAMPLES) + 1)
    da = (a[::factor] - np.mean(a[::factor])).astype(np.float64)
    db = (b[::factor] - np.mean(b[::factor])).astype(np.float64)
    n1, n2 = float(np.linalg.norm(da)), float(np.linalg.norm(db))
    if n1 == 0 or n2 == 0:
        return 0
    corr = correlate(da / n1, db / n2, mode="full")
    # scipy correlate(a,b)[k] = Σ a[n+k]*b[n] 이므로, B가 d만큼 늦으면 피크는 k=-d에 온다.
    lag = int(np.argmax(corr)) - (len(db) - 1)
    return -lag * factor


def _to_mono(data: np.ndarray, channel: Any) -> np.ndarray:
    if data.ndim == 1:
        return data.astype(np.float32)
    if channel == "mix" or channel is None:
        return np.mean(data, axis=1).astype(np.float32)
    ch = int(channel)
    if ch < 0 or ch >= data.shape[1]:
        raise ValueError(f"channel {ch} out of range (0~{data.shape[1] - 1})")
    return data[:, ch].astype(np.float32)


class WavCompareService:
    """A/B WAV 세션 보관 + 시작점 매칭 + 구간별 종합 비교.

    _params는 파형비교 위젯과 테스트 실행기가 공유하는 비교 설정(단일 진실)이다.
    channel은 공유 대상이 아니다 -- 위젯 표시용 로컬 값이고, 실행기는 항상
    channel="all"(전채널 AND)로 비교한다.
    """

    def __init__(self, work_dir: Path):
        self._work_dir = work_dir
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._meta: dict[str, Optional[dict]] = {"a": None, "b": None}
        self._params: dict = dict(DEFAULT_PARAMS)
        self._last_result: Optional[dict] = None

    # ---- shared params (위젯 <-> 실행기 동기화) ----------------------------

    def get_params(self) -> dict:
        return dict(self._params)

    def set_params(self, patch: dict) -> dict:
        """공유 비교 설정 갱신 -- 값 검증 후 저장. 알 수 없는 키는 무시한다."""
        params = dict(self._params)
        if "algorithm" in patch and patch["algorithm"] is not None:
            if patch["algorithm"] not in ALGORITHMS:
                raise ValueError(f"unknown algorithm: {patch['algorithm']}")
            params["algorithm"] = patch["algorithm"]
        if "threshold" in patch and patch["threshold"] is not None:
            threshold = float(patch["threshold"])
            if not 0.0 <= threshold <= 1.0:
                raise ValueError("threshold must be within 0~1")
            params["threshold"] = threshold
        if "segment_ms" in patch and patch["segment_ms"] is not None:
            segment_ms = int(patch["segment_ms"])
            if segment_ms <= 0:
                raise ValueError("segment_ms must be positive")
            params["segment_ms"] = segment_ms
        if "align" in patch and patch["align"] is not None:
            params["align"] = bool(patch["align"])
        self._params = params
        return self.get_params()

    # ---- upload / status / series --------------------------------------

    def _slot_path(self, which: str) -> Path:
        return self._work_dir / f"{which}.wav"

    @staticmethod
    def _read_wav(path: Path) -> tuple[int, np.ndarray]:
        from scipy.io.wavfile import read as wav_read

        try:
            sr, wav = wav_read(str(path))
        except Exception as exc:
            raise ValueError(f"WAV 파싱 실패: {exc}")
        return int(sr), np.asarray(wav)

    @staticmethod
    def _meta_for(sr: int, wav: np.ndarray, filename: str) -> dict:
        channels = int(wav.shape[1]) if wav.ndim > 1 else 1
        n = int(wav.shape[0])
        return {
            "filename": filename,
            "samplerate": int(sr),
            "channels": channels,
            "samples": n,
            "duration_s": round(n / float(sr), 3),
        }

    def publish_files(self, path_a: Path, path_b: Path, label_a: str, label_b: str) -> dict:
        """비교한 2파일을 A/B 슬롯에 복사해 위젯에 표시한다 (다음 비교 때 갱신)."""
        for which, src in (("a", path_a), ("b", path_b)):
            self._slot_path(which).write_bytes(Path(src).read_bytes())
        sr_a, wav_a = self._read_wav(self._slot_path("a"))
        sr_b, wav_b = self._read_wav(self._slot_path("b"))
        self._meta["a"] = self._meta_for(sr_a, wav_a, label_a)
        self._meta["b"] = self._meta_for(sr_b, wav_b, label_b)
        return self.status()

    def upload(self, which: str, data: bytes, filename: str) -> dict:
        if which not in ("a", "b"):
            raise ValueError("which must be 'a' or 'b'")
        if not audio_service._ensure_compare_deps():
            raise RuntimeError("librosa/scikit-learn이 설치되어 있지 않습니다")
        self._slot_path(which).write_bytes(data)
        sr, wav = self._read_wav(self._slot_path(which))
        self._meta[which] = self._meta_for(sr, wav, filename)
        return self._meta[which]

    def _last_summary(self) -> Optional[dict]:
        if self._last_result is None:
            return None
        r = self._last_result["result"]
        return {
            "source": self._last_result["source"],
            "ts": self._last_result["ts"],
            "a_label": self._last_result["a_label"],
            "b_label": self._last_result["b_label"],
            "overall_pass": r["overall_pass"],
            "pass_rate": r["pass_rate"],
            "offset_ms": r["offset_ms"],
            "segment_count": r["segment_count"],
        }

    def status(self) -> dict:
        return {"a": self._meta["a"], "b": self._meta["b"],
                "params": self.get_params(), "last": self._last_summary()}

    def last_result(self) -> dict:
        if self._last_result is None:
            raise FileNotFoundError("비교 결과가 없습니다")
        return self._last_result

    def series(self, which: str, max_points: int = 2000, channel: Any = "mix") -> dict:
        meta = self._meta.get(which)
        if meta is None:
            raise FileNotFoundError(f"슬롯 {which}에 업로드된 파일이 없습니다")
        from scipy.io.wavfile import read as wav_read

        _sr, wav = wav_read(str(self._slot_path(which)))
        mono = _to_mono(np.asarray(wav), channel).astype(np.float64)
        n = len(mono)
        cols = max(1, min(int(max_points), n))
        edges = np.linspace(0, n, cols + 1).astype(int)
        points = []
        sr = int(meta["samplerate"])
        for i in range(cols):
            s, e = int(edges[i]), int(edges[i + 1])
            chunk = mono[s:e] if e > s else mono[s:s + 1]
            points.append({
                "t_ms": round(s / sr * 1000.0, 2),
                "min": float(np.min(chunk)),
                "max": float(np.max(chunk)),
            })
        return {"samplerate": sr, "channel": channel, "points": points}

    # ---- compare ---------------------------------------------------------

    @staticmethod
    def _load_file(path: Path, channel: Any, target_sr: int) -> np.ndarray:
        from scipy.io.wavfile import read as wav_read

        sr, wav = wav_read(str(path))
        mono = _to_mono(np.asarray(wav), channel).astype(np.float64)
        if int(sr) != target_sr:
            mono = np.asarray(
                audio_service.librosa.resample(mono, orig_sr=int(sr), target_sr=target_sr),
                dtype=np.float64,
            )
        return mono

    @staticmethod
    def _channel_count(path: Path) -> int:
        from scipy.io.wavfile import read as wav_read

        _sr, wav = wav_read(str(path))
        wav = np.asarray(wav)
        return int(wav.shape[1]) if wav.ndim > 1 else 1

    def _analyze(
        self,
        a: np.ndarray,
        b: np.ndarray,
        sr: int,
        algorithm: str,
        threshold: float,
        segment_ms: int,
        align: bool,
        channel_label: Any,
    ) -> dict:
        """정렬 + 구간별 비교 본체 (단일 채널 스트림 기준)."""
        offset_samples = _find_offset_samples(a, b) if align else 0
        if offset_samples > 0:
            b = b[offset_samples:]
        elif offset_samples < 0:
            a = a[-offset_samples:]
        n = min(len(a), len(b))
        if n < 10:
            raise ValueError("정렬 후 겹치는 구간이 너무 짧습니다 (10샘플 미만)")
        a, b = a[:n], b[:n]
        offset_ms = round(offset_samples / sr * 1000.0, 2)

        seg_len = max(10, int(segment_ms * sr / 1000))
        segments = []
        for i in range(0, n, seg_len):
            sa, sb = a[i:i + seg_len], b[i:i + seg_len]
            if len(sa) < 10:
                break
            try:
                sims = _segment_sims(sa, sb, sr)
                passed, value = _segment_pass(algorithm, sims, threshold)
            except Exception as exc:
                # 너무 짧은 구간 등 지표 계산 실패 -- 실패 구간으로 기록
                sims = {}
                passed, value = False, 0.0
                segments.append({
                    "index": len(segments),
                    "t_start_ms": round(i / sr * 1000.0, 2),
                    "t_end_ms": round(min(i + seg_len, n) / sr * 1000.0, 2),
                    "pass": passed,
                    "value": value,
                    "sims": sims,
                    "error": str(exc),
                })
                continue
            segments.append({
                "index": len(segments),
                "t_start_ms": round(i / sr * 1000.0, 2),
                "t_end_ms": round(min(i + seg_len, n) / sr * 1000.0, 2),
                "pass": passed,
                "value": round(value, 4),
                "sims": {k: round(v, 4) for k, v in sims.items()},
            })

        full_sims = _segment_sims(a, b, sr)
        _, full_value = _segment_pass(algorithm, full_sims, threshold)
        passed_count = sum(1 for s in segments if s["pass"])
        overall_pass = len(segments) > 0 and passed_count == len(segments)
        return {
            "algorithm": algorithm,
            "threshold": threshold,
            "segment_ms": segment_ms,
            "align": align,
            "channel": channel_label,
            "samplerate": sr,
            "offset_ms": offset_ms,
            "overall_pass": overall_pass,
            "segment_count": len(segments),
            "passed_count": passed_count,
            "pass_rate": round(passed_count / len(segments), 4) if segments else 0.0,
            "overall_value": round(full_value, 4),
            "overall_sims": {k: round(v, 4) for k, v in full_sims.items()},
            "segments": segments,
        }

    @staticmethod
    def _validate_params(algorithm: str, threshold: float, segment_ms: int) -> tuple[str, float, int]:
        if algorithm not in ALGORITHMS:
            raise ValueError(f"unknown algorithm: {algorithm}")
        threshold = float(threshold)
        segment_ms = int(segment_ms)
        if segment_ms <= 0:
            raise ValueError("segment_ms must be positive")
        return algorithm, threshold, segment_ms

    def _run_channels(
        self,
        path_a: Path,
        path_b: Path,
        sr: int,
        channel: Any,
        algorithm: str,
        threshold: float,
        segment_ms: int,
        align: bool,
    ) -> dict:
        """channel="all"이면 전채널 독립 비교 후 AND, 아니면 단일 스트림 비교."""
        if channel == "all":
            n_ch = min(self._channel_count(path_a), self._channel_count(path_b))
            if n_ch < 1:
                raise ValueError("비교할 채널이 없습니다")
            per_channel = []
            for ch in range(n_ch):
                a = self._load_file(path_a, ch, sr)
                b = self._load_file(path_b, ch, sr)
                per_channel.append(self._analyze(a, b, sr, algorithm, threshold, segment_ms, align, ch))
            overall_pass = all(p["overall_pass"] for p in per_channel)
            # 구간 상세는 첫 실패 채널(전부 통과면 ch0)의 것을 대표로 싣는다
            rep = next((p for p in per_channel if not p["overall_pass"]), per_channel[0])
            merged = dict(rep)
            merged["channel"] = "all"
            merged["overall_pass"] = overall_pass
            merged["per_channel"] = [
                {k: p[k] for k in ("channel", "overall_pass", "segment_count",
                                  "passed_count", "pass_rate", "overall_value", "offset_ms")}
                for p in per_channel
            ]
            return merged
        a = self._load_file(path_a, channel, sr)
        b = self._load_file(path_b, channel, sr)
        return self._analyze(a, b, sr, algorithm, threshold, segment_ms, align, channel)

    def compare_files(
        self,
        path_a: Path | str,
        path_b: Path | str,
        label_a: str,
        label_b: str,
        source: str,
        algorithm: str = "comprehensive",
        threshold: float = DEFAULT_THRESHOLD,
        segment_ms: int = DEFAULT_SEGMENT_MS,
        align: bool = True,
        channel: Any = "all",
        publish: bool = True,
    ) -> dict:
        """임의 경로 2파일 비교 (테스트 실행기용). 비교 후 슬롯 게시 + 결과 저장."""
        algorithm, threshold, segment_ms = self._validate_params(algorithm, threshold, segment_ms)
        path_a, path_b = Path(path_a), Path(path_b)
        if not path_a.exists():
            raise FileNotFoundError(f"비교 파일 없음: {label_a}")
        if not path_b.exists():
            raise FileNotFoundError(f"비교 파일 없음: {label_b}")
        if not audio_service._ensure_compare_deps():
            raise RuntimeError("librosa/scikit-learn이 설치되어 있지 않습니다")
        sr_a, _ = self._read_wav(path_a)
        sr_b, _ = self._read_wav(path_b)
        sr = max(sr_a, sr_b)
        result = self._run_channels(path_a, path_b, sr, channel, algorithm, threshold, segment_ms, align)
        import time

        self._last_result = {
            "result": result, "a_label": label_a, "b_label": label_b,
            "source": source, "ts": time.time(),
        }
        if publish:
            self.publish_files(path_a, path_b, label_a, label_b)
        return result

    def compare(
        self,
        algorithm: str = "comprehensive",
        threshold: float = DEFAULT_THRESHOLD,
        segment_ms: int = DEFAULT_SEGMENT_MS,
        align: bool = True,
        channel: Any = "mix",
    ) -> dict:
        """슬롯 A/B 비교 (파형비교 위젯용)."""
        algorithm, threshold, segment_ms = self._validate_params(algorithm, threshold, segment_ms)
        if self._meta["a"] is None or self._meta["b"] is None:
            raise FileNotFoundError("A/B 두 파일을 모두 업로드하세요")
        if not audio_service._ensure_compare_deps():
            raise RuntimeError("librosa/scikit-learn이 설치되어 있지 않습니다")

        sr = max(int(self._meta["a"]["samplerate"]), int(self._meta["b"]["samplerate"]))
        result = self._run_channels(
            self._slot_path("a"), self._slot_path("b"), sr,
            channel, algorithm, threshold, segment_ms, align)
        import time

        self._last_result = {
            "result": result,
            "a_label": self._meta["a"]["filename"],
            "b_label": self._meta["b"]["filename"],
            "source": "widget", "ts": time.time(),
        }
        return result
