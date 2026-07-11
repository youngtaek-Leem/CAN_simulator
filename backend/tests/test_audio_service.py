import numpy as np
import pytest
from scipy.io.wavfile import write as wav_write

from audio_service import AudioService

SR = 22050


def stereo(mono: np.ndarray) -> np.ndarray:
    return np.stack([mono, mono], axis=1)


def tone(freq: float, duration_s: float = 2.0, amp: float = 0.5) -> np.ndarray:
    t = np.linspace(0, duration_s, int(SR * duration_s), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)


@pytest.fixture
def dirs(tmp_path):
    rec_dir = tmp_path / "rec"
    golden_dir = tmp_path / "golden"
    rec_dir.mkdir()
    golden_dir.mkdir()
    return rec_dir, golden_dir


def test_device_listing_does_not_crash_without_hardware(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    info = svc.info()
    # sounddevice is installed in this environment (even headless CI usually
    # reports at least a null/default device list), so this should not error;
    # if it genuinely has no backend, initialized just stays False -- either
    # way, no exception.
    assert "devices" in info


def test_start_rejected_without_device_selected(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc.initialized = True
    svc.device_index = None
    r = svc.start("x.wav")
    assert r["ok"] is False


def test_compare_missing_files_reports_reason(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    r = svc.compare("missing.wav", "also_missing.wav")
    assert r["ok"] is False
    assert "reason" in r


def test_compare_identical_signal_passes(dirs):
    rec_dir, golden_dir = dirs
    sig = tone(440)
    wav_write(str(golden_dir / "g.wav"), SR, stereo(sig))
    wav_write(str(rec_dir / "same.wav"), SR, stereo(sig))

    svc = AudioService(rec_dir, golden_dir)
    result = svc.compare("same.wav", "g.wav", threshold=0.8)
    assert result["ok"] is True
    assert result["channels"]["ch0"]["mfcc_dtw_similarity"] > 0.99


def test_compare_silence_against_tone_fails(dirs):
    rec_dir, golden_dir = dirs
    wav_write(str(golden_dir / "g.wav"), SR, stereo(tone(440)))
    wav_write(str(rec_dir / "silence.wav"), SR, stereo(np.zeros(SR * 2, dtype=np.int16)))

    svc = AudioService(rec_dir, golden_dir)
    result = svc.compare("silence.wav", "g.wav", threshold=0.8)
    assert result["ok"] is False
    assert result["channels"]["ch0"]["mfcc_dtw_similarity"] < 0.8


def test_compare_returns_all_seven_metrics(dirs):
    rec_dir, golden_dir = dirs
    wav_write(str(golden_dir / "g.wav"), SR, stereo(tone(440)))
    wav_write(str(rec_dir / "rec.wav"), SR, stereo(tone(440)))

    svc = AudioService(rec_dir, golden_dir)
    result = svc.compare("rec.wav", "g.wav")
    metrics = result["channels"]["ch0"]
    assert set(metrics) == {
        "mfcc_dtw_similarity",
        "fft_similarity",
        "fft_band_similarity",
        "cross_corr_similarity",
        "rms_diff",
        "zcr_diff",
        "centroid_diff",
    }


def test_save_as_golden_copies_file(dirs):
    rec_dir, golden_dir = dirs
    wav_write(str(rec_dir / "rec.wav"), SR, stereo(tone(440)))
    svc = AudioService(rec_dir, golden_dir)
    r = svc.save_as_golden("rec.wav", "case1_golden.wav")
    assert r["ok"] is True
    assert (golden_dir / "case1_golden.wav").exists()


def test_save_as_golden_missing_source(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    r = svc.save_as_golden("nope.wav", "golden.wav")
    assert r["ok"] is False
