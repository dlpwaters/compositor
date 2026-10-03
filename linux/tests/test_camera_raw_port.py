"""CameraRawTests.swift controls and shared C ABI regression."""

import pytest
from compositor_linux import engine
from compositor_linux.dialogs import FilterDialog
from PIL import Image
from PySide6.QtWidgets import QApplication


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_camera_raw_identity_and_light_color():
    source = Image.new("RGBA", (3, 3), (128, 128, 128, 128))
    assert engine.filtered(source, "Camera Raw Filter", {}).tobytes() == source.tobytes()
    raised = engine.filtered(source, "Camera Raw Filter", {"exposure": 1})
    assert 173 <= raised.getpixel((1, 1))[0] <= 179
    assert raised.getpixel((1, 1))[3] == 128
    warm = engine.filtered(source, "Camera Raw Filter", {"temperature": 100})
    assert warm.getpixel((1, 1))[0] > warm.getpixel((1, 1))[2]


def test_camera_raw_curve_grading_detail_and_calibration_are_active():
    source = Image.new("RGBA", (15, 15), (120, 80, 40, 255))
    settings = {
        "curve": {"rgb": [{"x": 0, "y": 0.2}, {"x": 1, "y": 1}]},
        "grading": {"global": {"hue": 240, "saturation": 60}},
        "calibration": {"redSaturation": 70},
        "detail": {"noiseColor": 50},
    }
    result = engine.filtered(source, "Camera Raw Filter", settings)
    assert result.getpixel((7, 7)) != source.getpixel((7, 7))
    assert result.getpixel((7, 7))[3] == 255


def test_camera_raw_effects_and_optics_are_active():
    source = Image.new("RGBA", (32, 32), (180, 120, 80, 255))
    output = engine.filtered(
        source, "Camera Raw Filter", {"vignetteAmount": -100, "optics": {"vignetteAmount": 50}}
    )
    assert output.getpixel((0, 0)) != source.getpixel((0, 0))


def test_camera_raw_geometry_moves_pixels_and_rejects_bad_settings():
    source = Image.new("RGBA", (20, 20))
    source.putpixel((4, 4), (255, 0, 0, 255))
    result = engine.filtered(source, "Camera Raw Filter", {"geometry": {"offsetX": 50}})
    assert result.tobytes() != source.tobytes()
    with pytest.raises(ValueError):
        engine.filtered(source, "Camera Raw Filter", {"geometry": {"offsetX": float("nan")}})


def test_camera_raw_auto_white_balance_neutralizes_color_cast():
    source = Image.new("RGBA", (8, 8), (180, 120, 90, 255))
    result = engine.filtered(source, "Camera Raw Filter", {"whiteBalance": "Auto"})
    red, green, blue, alpha = result.getpixel((4, 4))
    assert max(red, green, blue) - min(red, green, blue) < 50
    assert alpha == 255


def test_camera_raw_calibration_process_versions_are_distinct():
    source = Image.new("RGBA", (8, 8), (180, 100, 70, 255))
    early = engine.filtered(
        source, "Camera Raw Filter", {"calibration": {"redHue": 70, "process": "Version 1"}}
    )
    current = engine.filtered(
        source, "Camera Raw Filter", {"calibration": {"redHue": 70, "process": "Version 6"}}
    )
    assert early.tobytes() != current.tobytes()


def test_camera_raw_dialog_roundtrips_nested_controls(qapp):
    dialog = FilterDialog("Camera Raw Filter", {"exposure": 1, "grading": {"global": {"hue": 230}}})
    assert dialog.value("Exposure") == 1
    dialog.fields["Mixer Hue Reds"].setValue(25)
    result = dialog.result_settings()
    assert result["grading"]["global"]["hue"] == 230
    assert result["mixer"]["hue"][0] == 25
    assert dialog.value("White Balance") == "Custom"
    dialog.fields["White Balance"].setCurrentText("Auto")
    dialog.fields["Calibration process"].setCurrentText("Version 2")
    dialog.fields["Curve shadowSplit"].setValue(30)
    dialog.fields["Grading blending"].setValue(70)
    dialog.fields["Mixer point colors JSON"].setText('[{"hue":20,"hueShift":30}]')
    dialog.fields["Geometry guides JSON"].setText('[{"startX":0,"startY":0,"endX":1,"endY":0}]')
    settings = dialog.result_settings()
    assert settings["whiteBalance"] == "Auto"
    assert settings["calibration"]["process"] == "Version 2"
    assert settings["curve"]["shadowSplit"] == 30
    assert settings["grading"]["blending"] == 70
    assert settings["mixer"]["points"][0]["hueShift"] == 30
    assert settings["geometry"]["guides"][0]["endX"] == 1
    dialog.timer.stop()
    dialog.close()


def test_camera_raw_dialog_defaults_leave_pixels_unchanged(qapp):
    dialog = FilterDialog("Camera Raw Filter")
    source = Image.new("RGBA", (12, 12), (100, 140, 180, 255))
    assert (
        engine.filtered(source, "Camera Raw Filter", dialog.result_settings()).tobytes()
        == source.tobytes()
    )
    dialog.timer.stop()
    dialog.close()


@pytest.mark.parametrize(
    "settings",
    [
        {"detail": {"noiseColor": float("nan")}},
        {"optics": {"distortion": 1e99}},
        {"calibration": {"process": "Unknown"}},
        {"curve": {"shadows": 1e99}},
        {"mixer": {"points": [None]}},
        {"geometry": {"guides": [None], "upright": "Guided"}},
        {"grading": {"blending": float("nan")}},
        {"mixer": {"points": [{"saturation": 5}]}},
        {"grainSeed": -1},
        {"whiteBalance": "Automatic"},
        {"kind": "Other Filter"},
        {"preview": "yes"},
    ],
)
def test_camera_raw_nested_values_refuse_before_native_call(settings):
    with pytest.raises(ValueError):
        engine.filtered(
            Image.new("RGBA", (8, 8), (100, 90, 80, 255)), "Camera Raw Filter", settings
        )
