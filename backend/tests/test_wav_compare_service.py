import numpy as np
import pytest
from scipy.io.wavfile import write as wav_write

from wav_compare_service import (
    ALGORITHMS,
    WavCompareService,
    _find_offset_samples,
    _rel_similarity,
)

SR = 22050


def tone(freq: float, duration_s: float = 1.0, amp: float = 0.5, sr: int = SR) -> np.ndarray:
    t = np.linspace(0, duration_s, int(sr * duration_s), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)


def stereo(mono: np.ndarray) -> np.ndarray:
    return np.stack([mono, mono], axis=1)


@pytest.fixture
def svc(tmp_path):
    return WavCompareService(tmp_path / "wavcmp")


def _upload(svc, which, data: np.ndarray, sr: int = SR, name: str = "f.wav"):
    return svc.upload(which, _wav_bytes(data, sr), name)


def _wav_bytes(data: np.ndarray, sr: int) -> bytes:
    import io

    buf = io.BytesIO()
    from scipy.io.wavfile import write as w

    w(buf, sr, data)
    return buf.getvalue()


def test_rel_similarity_basics():
    assert _rel_similarity(5.0, 5.0) == 1.0
    assert _rel_similarity(0.0, 0.0) == 1.0
    assert _rel_similarity(1.0, 0.0) == pytest.approx(0.0)
    assert 0.0 < _rel_similarity(1.0, 0.9) < 1.0


def test_upload_and_status(svc):
    info = _upload(svc, "a", stereo(tone(440)), name="a.wav")
    assert info["filename"] == "a.wav"
    assert info["samplerate"] == SR
    assert info["channels"] == 2
    st = svc.status()
    assert st["a"]["filename"] == "a.wav" and st["b"] is None
    assert st["params"]["algorithm"] == "comprehensive"
    assert st["last"] is None


def test_compare_identical_passes(svc):
    sig = stereo(tone(440))
    _upload(svc, "a", sig)
    _upload(svc, "b", sig)
    r = svc.compare()
    assert r["overall_pass"] is True
    assert r["pass_rate"] == 1.0
    assert r["offset_ms"] == pytest.approx(0.0)
    assert r["segment_count"] == 10  # 1s / 100ms
    assert all(s["pass"] for s in r["segments"])
    assert set(r["overall_sims"]) == set(ALGORITHMS) - {"comprehensive"}


def test_compare_silence_vs_tone_fails(svc):
    _upload(svc, "a", stereo(tone(440)))
    _upload(svc, "b", stereo(np.zeros(SR, dtype=np.int16)))
    r = svc.compare()
    assert r["overall_pass"] is False
    assert r["pass_rate"] == 0.0


def test_align_detects_delayed_start(svc):
    a = tone(440)
    b = np.concatenate([np.zeros(int(SR * 0.3), dtype=np.int16), a])
    _upload(svc, "a", stereo(a))
    _upload(svc, "b", stereo(b))
    r = svc.compare(align=True)
    assert r["offset_ms"] == pytest.approx(300.0, abs=15.0)
    assert r["overall_pass"] is True


def test_no_align_fails_on_delayed_start(svc):
    a = tone(440)
    b = np.concatenate([np.zeros(int(SR * 0.3), dtype=np.int16), a])
    _upload(svc, "a", stereo(a))
    _upload(svc, "b", stereo(b))
    r = svc.compare(align=False)
    assert r["offset_ms"] == 0.0
    assert r["overall_pass"] is False


def test_different_samplerates_compared_via_resample(svc):
    _upload(svc, "a", stereo(tone(440, sr=SR), ), sr=SR)
    _upload(svc, "b", stereo(tone(440, duration_s=1.0, sr=SR * 2)), sr=SR * 2)
    r = svc.compare()
    assert r["samplerate"] == SR * 2
    assert r["overall_pass"] is True


def test_individual_algorithm_rms_catches_amplitude_change(svc):
    _upload(svc, "a", stereo(tone(440, amp=0.5)))
    _upload(svc, "b", stereo(tone(440, amp=0.1)))
    r_rms = svc.compare(algorithm="rms")
    assert r_rms["overall_pass"] is False
    r_mfcc = svc.compare(algorithm="mfcc_dtw")
    assert r_mfcc["overall_pass"] is True  # 같은 주파수 shape는 유사


def test_find_offset_samples_direction():
    sr = 8000
    a = np.sin(2 * np.pi * 440 * np.arange(sr) / sr)
    b = np.concatenate([np.zeros(800), a])  # B가 0.1s 늦음
    assert _find_offset_samples(a, b) == pytest.approx(800, abs=8)


def test_compare_errors(svc):
    with pytest.raises(FileNotFoundError):
        svc.compare()
    _upload(svc, "a", stereo(tone(440)))
    _upload(svc, "b", stereo(tone(440)))
    with pytest.raises(ValueError):
        svc.compare(algorithm="nope")
    with pytest.raises(ValueError):
        svc.compare(segment_ms=0)
    with pytest.raises(FileNotFoundError):
        svc.series("c")


def test_series_downsampling(svc):
    _upload(svc, "a", stereo(tone(440, duration_s=2.0)))
    s = svc.series("a", max_points=100)
    assert len(s["points"]) == 100
    assert s["points"][0]["t_ms"] == pytest.approx(0.0)
    assert s["points"][-1]["t_ms"] > 1000
    assert all(p["min"] <= p["max"] for p in s["points"])


def _write_wav(path, data: np.ndarray, sr: int = SR):
    wav_write(str(path), sr, data)


def test_channel_all_passes_on_identical_stereo(svc, tmp_path):
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _write_wav(a, stereo(tone(440)))
    _write_wav(b, stereo(tone(440)))
    r = svc.compare_files(a, b, "a.wav", "b.wav", source="runner", channel="all")
    assert r["channel"] == "all"
    assert r["overall_pass"] is True
    assert len(r["per_channel"]) == 2
    assert all(p["overall_pass"] for p in r["per_channel"])


def test_channel_all_fails_if_any_channel_differs(svc, tmp_path):
    ch_ok = tone(440)
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    _write_wav(a, stereo(ch_ok))
    mixed = np.stack([ch_ok, np.zeros(SR, dtype=np.int16)], axis=1)
    _write_wav(b, mixed)
    r = svc.compare_files(a, b, "a.wav", "b.wav", source="runner", channel="all")
    assert r["overall_pass"] is False
    assert [p["overall_pass"] for p in r["per_channel"]] == [True, False]


def test_compare_files_publishes_slots_and_last_result(svc, tmp_path):
    a = tmp_path / "prev.wav"
    b = tmp_path / "curr.wav"
    _write_wav(a, stereo(tone(440)))
    _write_wav(b, stereo(tone(440)))
    r = svc.compare_files(a, b, "prev.wav", "curr.wav", source="runner")
    assert r["overall_pass"] is True
    st = svc.status()
    assert st["a"]["filename"] == "prev.wav"
    assert st["b"]["filename"] == "curr.wav"
    assert st["last"]["source"] == "runner"
    assert st["last"]["overall_pass"] is True
    assert st["last"]["a_label"] == "prev.wav"
    full = svc.last_result()
    assert full["result"]["overall_pass"] is True
    assert full["b_label"] == "curr.wav"
    s = svc.series("b", max_points=10)
    assert len(s["points"]) == 10


def test_compare_files_missing_file(svc, tmp_path):
    with pytest.raises(FileNotFoundError):
        svc.compare_files(tmp_path / "no.wav", tmp_path / "no2.wav", "no.wav", "no2.wav", source="runner")


def test_widget_compare_stores_last_result(svc):
    _upload(svc, "a", stereo(tone(440)))
    _upload(svc, "b", stereo(tone(440)))
    svc.compare()
    assert svc.status()["last"]["source"] == "widget"


def test_shared_params_get_set_validation(svc):
    assert svc.get_params() == {
        "algorithm": "comprehensive", "threshold": 0.8,
        "segment_ms": 100, "align": True,
    }
    out = svc.set_params({"algorithm": "mfcc_dtw", "threshold": 0.5, "segment_ms": 50, "align": False})
    assert out == {"algorithm": "mfcc_dtw", "threshold": 0.5, "segment_ms": 50, "align": False}
    assert svc.get_params() == out
    # 부분 갱신 + 알 수 없는 키 무시
    assert svc.set_params({"threshold": 0.9, "bogus": 1})["threshold"] == 0.9
    with pytest.raises(ValueError):
        svc.set_params({"algorithm": "nope"})
    with pytest.raises(ValueError):
        svc.set_params({"threshold": 1.5})
    with pytest.raises(ValueError):
        svc.set_params({"segment_ms": 0})
