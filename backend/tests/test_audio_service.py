import time
from unittest.mock import patch

import numpy as np
import pytest
from scipy.io.wavfile import write as wav_write

from audio_service import (
    AudioService,
    _ChannelLevelTracker,
    DEFAULT_CHANNELS,
    choose_channel_count,
)

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


# ---- refresh_devices(): input-only, host-API deduplication ----------------
#
# Bug report: on Windows, the device list showed output devices too.
# refresh_devices() now filters server-side (once, instead of leaving every
# frontend widget to remember to filter) and additionally restricts to the
# default host API, since Windows commonly enumerates the same physical
# device once per host API (MME/DirectSound/WASAPI/WDM-KS) and some of
# those duplicate entries misreport channel counts.


def test_refresh_devices_excludes_output_only_devices(dirs):
    rec_dir, golden_dir = dirs
    fake_devices = [
        {"name": "Microphone", "max_input_channels": 2, "hostapi": 0},
        {"name": "Speakers", "max_input_channels": 0, "hostapi": 0},
    ]
    svc = AudioService(rec_dir, golden_dir)
    with patch("audio_service.sd.query_devices", return_value=fake_devices), \
         patch("audio_service.sd.default") as fake_default:
        fake_default.hostapi = 0
        svc.refresh_devices()

    names = [d["name"] for d in svc.info()["devices"]]
    assert names == ["Microphone"]


def test_refresh_devices_restricts_to_default_hostapi_to_avoid_windows_duplicates(dirs):
    """Simulates the Windows scenario: the same physical mic enumerated
    under both MME (hostapi 0, the default) and WASAPI (hostapi 2), plus a
    WASAPI entry that misreports input channels for what is actually an
    output device. Only the default-hostapi mic should survive."""
    rec_dir, golden_dir = dirs
    fake_devices = [
        {"name": "Mic [MME]", "max_input_channels": 2, "hostapi": 0},
        {"name": "Speakers [MME]", "max_input_channels": 0, "hostapi": 0},
        {"name": "Mic [WASAPI]", "max_input_channels": 2, "hostapi": 2},
        {"name": "Speakers [WASAPI, misreported]", "max_input_channels": 2, "hostapi": 2},
    ]
    svc = AudioService(rec_dir, golden_dir)
    with patch("audio_service.sd.query_devices", return_value=fake_devices), \
         patch("audio_service.sd.default") as fake_default:
        fake_default.hostapi = 0
        svc.refresh_devices()

    names = [d["name"] for d in svc.info()["devices"]]
    assert names == ["Mic [MME]"]


def test_refresh_devices_falls_back_to_other_hostapis_if_default_has_no_input(dirs):
    """A real microphone attached under a non-default host API must never
    disappear entirely just because the default host API happens to have
    no input devices of its own."""
    rec_dir, golden_dir = dirs
    fake_devices = [
        {"name": "Speakers [MME]", "max_input_channels": 0, "hostapi": 0},
        {"name": "USB Mic [WASAPI]", "max_input_channels": 1, "hostapi": 2},
    ]
    svc = AudioService(rec_dir, golden_dir)
    with patch("audio_service.sd.query_devices", return_value=fake_devices), \
         patch("audio_service.sd.default") as fake_default:
        fake_default.hostapi = 0
        svc.refresh_devices()

    names = [d["name"] for d in svc.info()["devices"]]
    assert names == ["USB Mic [WASAPI]"]


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


# ---- 오디오 신호 모니터 (live level monitor) ------------------------------------


def test_channel_level_tracker_computes_peak_and_rms():
    tracker = _ChannelLevelTracker()
    samples = np.array([0.5, -0.8, 0.2, -0.1], dtype=np.float32)
    tracker.add_chunk(samples, now=0.0)
    assert tracker.peak == pytest.approx(0.8, abs=1e-6)
    assert tracker.rms == pytest.approx(float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))))


def test_channel_level_tracker_ignores_empty_chunk():
    tracker = _ChannelLevelTracker()
    tracker.add_chunk(np.array([], dtype=np.float32), now=0.0)
    assert tracker.peak == 0.0
    assert tracker.rms == 0.0
    assert len(tracker._raw_chunks) == 0


def test_channel_level_tracker_trims_raw_chunks_beyond_buffer_window():
    tracker = _ChannelLevelTracker()
    tracker.add_chunk(np.zeros(4, dtype=np.float32), now=0.0)
    tracker.add_chunk(np.zeros(4, dtype=np.float32), now=31.0)  # > RAW_BUFFER_SECONDS later
    assert len(tracker._raw_chunks) == 1  # the t=0.0 chunk was trimmed
    assert tracker._raw_chunks[0][0] == 31.0


def test_channel_level_tracker_reset_clears_state():
    tracker = _ChannelLevelTracker()
    tracker.add_chunk(np.array([0.9], dtype=np.float32), now=0.0)
    tracker.reset()
    assert tracker.peak == 0.0
    assert tracker.rms == 0.0
    assert len(tracker._raw_chunks) == 0


def test_get_level_default_state(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    level = svc.get_level()
    assert level == {
        "active": False,
        "recording": False,
        "monitoring": False,
        "owner": None,
        "channels": [
            {"index": 0, "peak": 0.0, "rms": 0.0},
            {"index": 1, "peak": 0.0, "rms": 0.0},
        ],
        "stream_started_at": None,
        "current_filename": None,
    }


# ---- waveform_slice() -- decimated per-pixel-column min/max, the data the
# zoomable waveform chart actually renders --------------------------------


def test_waveform_slice_empty_without_data():
    tracker = _ChannelLevelTracker()
    assert tracker.waveform_slice(from_s=0.0, to_s=1.0, max_points=10, samplerate=100) == []


def test_waveform_slice_invalid_range_returns_empty():
    tracker = _ChannelLevelTracker()
    tracker.add_chunk(np.ones(10, dtype=np.float32), now=0.0)
    assert tracker.waveform_slice(from_s=1.0, to_s=1.0, max_points=10, samplerate=100) == []
    assert tracker.waveform_slice(from_s=1.0, to_s=0.0, max_points=10, samplerate=100) == []


def test_waveform_slice_places_samples_in_correct_columns():
    tracker = _ChannelLevelTracker()
    # 100 samples/sec, chunk starts at t=0.0 -> samples at t=0.00, 0.01, ..., 0.09
    samples = np.linspace(-1.0, 1.0, 10, dtype=np.float32)
    tracker.add_chunk(samples, now=0.0)
    # ask for [0, 0.1) split into 2 columns -> first 5 samples in col0, last 5 in col1
    points = tracker.waveform_slice(from_s=0.0, to_s=0.1, max_points=2, samplerate=100)
    assert len(points) == 2
    assert points[0]["min"] == pytest.approx(float(samples[:5].min()))
    assert points[0]["max"] == pytest.approx(float(samples[:5].max()))
    assert points[1]["min"] == pytest.approx(float(samples[5:].min()))
    assert points[1]["max"] == pytest.approx(float(samples[5:].max()))


def test_waveform_slice_zoomed_out_merges_multiple_chunks_per_column():
    tracker = _ChannelLevelTracker()
    tracker.add_chunk(np.array([0.1, 0.2], dtype=np.float32), now=0.0)
    tracker.add_chunk(np.array([-0.9, 0.05], dtype=np.float32), now=0.02)
    # one wide column covering both chunks
    points = tracker.waveform_slice(from_s=0.0, to_s=0.04, max_points=1, samplerate=100)
    assert len(points) == 1
    assert points[0]["min"] == pytest.approx(-0.9)
    assert points[0]["max"] == pytest.approx(0.2)


def test_waveform_slice_ignores_chunks_outside_requested_range():
    tracker = _ChannelLevelTracker()
    tracker.add_chunk(np.array([0.7], dtype=np.float32), now=5.0)  # well before the window
    points = tracker.waveform_slice(from_s=0.0, to_s=1.0, max_points=10, samplerate=100)
    assert points == []


def test_get_waveform_before_any_stream_returns_empty_points(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    result = svc.get_waveform(from_ms=0.0, to_ms=1000.0, max_points=50)
    assert result["active"] is False
    assert result["samplerate"] is None
    assert all(ch["points"] == [] for ch in result["channels"])


def test_get_waveform_still_served_after_stream_closed(dirs):
    """오디오 신호 모니터 위젯의 Stop 이후 파형 탐색: stop()/stop_monitor()는
    _level_trackers를 지우지 않으므로, 스트림이 닫힌 뒤에도 최근
    RAW_BUFFER_SECONDS만큼은 계속 조회할 수 있어야 한다 (그래야 위젯이 정지
    시점 뷰를 고정하고 좌우로 스크롤/확대해서 탐색할 수 있다)."""
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._active_samplerate = SR
    now = time.time()
    svc._level_trackers[0].add_chunk(np.array([0.1, 0.2, 0.3], dtype=np.float32), now=now)

    # simulate Stop: stream closed, but (as in the real stop paths) the
    # level trackers are left untouched.
    svc._stream = None

    result = svc.get_waveform(from_ms=(now - 1) * 1000, to_ms=(now + 1) * 1000, max_points=10)
    assert result["active"] is False
    assert result["samplerate"] == SR
    ch0_points = result["channels"][0]["points"]
    assert len(ch0_points) > 0
    assert any(p["max"] > 0 for p in ch0_points)


def test_start_monitor_rejected_without_device_selected(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc.initialized = True
    svc.device_index = None
    r = svc.start_monitor()
    assert r["ok"] is False


def test_stop_monitor_is_noop_when_nothing_active(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    r = svc.stop_monitor()
    assert r["ok"] is True


def test_stop_monitor_refuses_while_recording_owns_the_stream(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    # simulate an in-progress test-runner recording without opening a real
    # device stream -- stop_monitor() must check ownership before touching
    # self._stream at all.
    svc._stream = object()
    svc._stream_owner = "recording"
    r = svc.stop_monitor()
    assert r["ok"] is False
    assert svc._stream is not None  # untouched


def test_stop_monitor_refuses_while_widget_record_owns_the_stream(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._stream = object()
    svc._stream_owner = "widget_record"
    r = svc.stop_monitor()
    assert r["ok"] is False
    assert svc._stream is not None  # untouched


def test_start_widget_recording_upgrades_an_existing_monitor_stream(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    sentinel = object()
    svc._stream = sentinel
    svc._stream_owner = "monitor"
    r = svc.start_widget_recording("monitor_case.wav")
    assert r["ok"] is True
    assert r["filename"] == "monitor_case.wav"
    assert svc._stream is sentinel
    assert svc._stream_owner == "widget_record"
    assert svc._is_recording is True


def test_start_widget_recording_rejected_when_test_runner_owns_the_stream(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._stream = object()
    svc._stream_owner = "recording"
    r = svc.start_widget_recording("monitor_case.wav")
    assert r["ok"] is False


def test_stop_widget_recording_refuses_for_other_owners(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._stream = object()
    svc._stream_owner = "recording"
    r = svc.stop_widget_recording()
    assert r["ok"] is False
    assert svc._stream is not None  # untouched, test-runner's recording is safe


def test_start_upgrades_an_existing_monitor_stream_in_place(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    sentinel = object()
    svc._stream = sentinel
    svc._stream_owner = "monitor"
    r = svc.start("case1.wav")
    assert r["ok"] is True
    assert r["filename"] == "case1.wav"
    assert svc._stream is sentinel  # same stream, not reopened
    assert svc._stream_owner == "recording"
    assert svc._wav_name == "case1.wav"
    assert svc._is_recording is True


def test_start_rejected_when_another_stream_already_active(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._stream = object()
    svc._stream_owner = "recording"
    r = svc.start("other.wav")
    assert r["ok"] is False


# ---- channel count adapts to the selected device (fixes PaErrorCode -9998 --
# a plain 1-channel mic can't satisfy a hardcoded 2-channel request) --------


def test_choose_channel_count_caps_at_default_channels_length():
    assert choose_channel_count(max_input_channels=8) == len(DEFAULT_CHANNELS)


def test_choose_channel_count_shrinks_for_a_mono_mic():
    assert choose_channel_count(max_input_channels=1) == 1


def test_choose_channel_count_zero_stays_zero():
    assert choose_channel_count(max_input_channels=0) == 0


# ---- 30-min segment rotation + stream_started_at (waveform x-axis 0s) -----


class _FakeStream:
    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def test_start_widget_recording_sets_stream_started_at(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._stream = _FakeStream()
    svc._stream_owner = "monitor"

    before = time.time()
    svc.start_widget_recording("monitor_case.wav")
    after = time.time()

    assert svc._stream_started_at is not None
    assert before <= svc._stream_started_at <= after
    assert svc._segment_started_at == svc._stream_started_at

    level = svc.get_level()
    assert level["stream_started_at"] == svc._stream_started_at
    assert level["current_filename"] == "monitor_case.wav"


def test_stop_clears_stream_started_at(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._stream = _FakeStream()
    svc._stream_owner = "monitor"
    svc._active_samplerate = SR
    svc._active_channels = 2
    svc.start_widget_recording("monitor_case.wav")
    assert svc._stream_started_at is not None

    result = svc.stop_widget_recording()

    assert result["ok"] is True
    assert svc._stream_started_at is None
    assert svc._segment_started_at is None
    assert svc.get_level()["stream_started_at"] is None
    assert (rec_dir / "monitor_case.wav").exists()


def test_monitor_only_stream_also_gets_a_stream_started_at(dirs):
    """Plain Start (monitor, no WAV) must set the x-axis anchor exactly like
    Record does -- the widget's waveform x-axis behaves identically in both
    modes, not just while an actual recording is in progress."""
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._active_samplerate = SR
    svc._active_channels = 1
    svc.device_index = 0
    svc.initialized = True

    with patch("audio_service.sd.query_devices", return_value={"default_samplerate": SR, "max_input_channels": 1}), \
         patch("audio_service.sd.InputStream", return_value=_FakeStream()):
        before = time.time()
        result = svc.start_monitor()
        after = time.time()

    assert result["ok"] is True
    assert svc._stream_started_at is not None
    assert before <= svc._stream_started_at <= after
    assert svc.get_level()["stream_started_at"] == svc._stream_started_at


def test_start_monitor_piggyback_does_not_reset_existing_anchor(dirs):
    """start_monitor() on an already-open stream (e.g. a recording already in
    progress) must not disturb that stream's own x-axis anchor."""
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._stream = _FakeStream()
    svc._stream_owner = "recording"
    svc._stream_started_at = 12345.0

    result = svc.start_monitor()

    assert result == {"ok": True, "already_active": True}
    assert svc._stream_started_at == 12345.0


def test_stop_monitor_clears_stream_started_at(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._stream = _FakeStream()
    svc._stream_owner = "monitor"
    svc._stream_started_at = time.time()

    result = svc.stop_monitor()

    assert result["ok"] is True
    assert svc._stream_started_at is None


def test_rotate_segment_writes_current_file_and_starts_a_new_one(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._stream = _FakeStream()
    svc._stream_owner = "widget_record"
    svc._is_recording = True
    svc._active_samplerate = SR
    svc._active_channels = 2
    svc._wav_name = "monitor_segment1.wav"
    svc._stream_started_at = time.time() - 10.0
    svc._segment_started_at = time.time() - 10.0
    svc._audio_data = [stereo(tone(440, duration_s=0.2))]

    svc._rotate_segment()

    assert (rec_dir / "monitor_segment1.wav").exists()
    assert svc._wav_name != "monitor_segment1.wav"
    assert svc._wav_name.startswith("monitor_") and svc._wav_name.endswith(".wav")
    assert svc._audio_data == []
    # the overall recording's x-axis anchor must survive a rotation --
    # only the per-segment timer resets.
    assert svc._stream_started_at == svc._stream_started_at
    assert svc._segment_started_at > time.time() - 1.0


def test_maybe_rotate_segment_noop_before_30_minutes(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._stream_owner = "widget_record"
    svc._is_recording = True
    svc._wav_name = "monitor_x.wav"
    svc._segment_started_at = time.time()  # just started

    svc._maybe_rotate_segment()

    assert svc._wav_name == "monitor_x.wav"  # unchanged, no rotation yet
    assert not (rec_dir / "monitor_x.wav").exists()  # nothing written


def test_maybe_rotate_segment_fires_after_30_minutes(dirs):
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._stream_owner = "widget_record"
    svc._is_recording = True
    svc._active_samplerate = SR
    svc._active_channels = 1
    svc._wav_name = "monitor_old.wav"
    svc._segment_started_at = time.time() - 1801  # just over 30 minutes ago
    svc._audio_data = []

    svc._maybe_rotate_segment()

    assert (rec_dir / "monitor_old.wav").exists()
    assert svc._wav_name != "monitor_old.wav"


def test_maybe_rotate_segment_ignores_test_runner_recording(dirs):
    """Only the widget's own Record splits into 30-min segments -- the test
    runner's start()/stop() recording (used for golden-file comparison)
    must always be saved as one complete file."""
    rec_dir, golden_dir = dirs
    svc = AudioService(rec_dir, golden_dir)
    svc._stream_owner = "recording"
    svc._is_recording = True
    svc._wav_name = "case1.wav"
    svc._segment_started_at = time.time() - 3600  # long past 30 min

    svc._maybe_rotate_segment()

    assert svc._wav_name == "case1.wav"  # never rotated
    assert not (rec_dir / "case1.wav").exists()
