"""RAW develop pipeline contract from RawImporter.swift."""

import sys
from types import SimpleNamespace

import numpy as np
import pytest
from compositor_linux import raw


def test_raw_develop_uses_local_decoder_and_user_controls(monkeypatch, tmp_path):
    calls = []

    class Frame:
        sizes = SimpleNamespace(width=2, height=1)
        camera_whitebalance = [2.0, 1.0, 1.5, 1.0]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def postprocess(self, **kwargs):
            calls.append(kwargs)
            return np.array([[[100, 80, 60], [40, 30, 20]]], dtype=np.uint8)

    monkeypatch.setitem(
        sys.modules,
        "rawpy",
        SimpleNamespace(imread=lambda _: Frame(), ColorSpace=SimpleNamespace(sRGB=1)),
    )
    path = tmp_path / "sample.dng"
    path.write_bytes(b"local raw fixture")
    result = raw.develop(path, dict(exposure=1, temperature=6500, tint=10, boost=0.5))
    assert result.size == (2, 1)
    assert result.getpixel((0, 0)) == (100, 80, 60, 255)
    assert calls[0]["exp_shift"] == 2
    assert calls[0]["user_wb"][0] > 2


def test_raw_rejects_bad_values_without_decoder(tmp_path):
    path = tmp_path / "bad.dng"
    path.write_bytes(b"bad")
    with pytest.raises(ValueError):
        raw.develop(path, {"exposure": float("nan")})
