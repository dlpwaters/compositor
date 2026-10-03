"""Bounded, transactional image import checks."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from compositor_linux import store
from compositor_linux.svg import MAX_SVG_BYTES


def test_svg_local_shapes_import_with_alpha(tmp_path):
    path = tmp_path / "mark.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8">'
        '<rect x="2" y="1" width="4" height="5" fill="#ff0000"/></svg>'
    )
    image = store.import_image(path)
    assert image.size == (12, 8)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((3, 3)) == (255, 0, 0, 255)


@pytest.mark.parametrize(
    "payload",
    [
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8"><image href="file:///etc/passwd"/></svg>',
        '<!DOCTYPE svg [<!ENTITY leak SYSTEM "file:///etc/passwd">]><svg width="12" height="8">&leak;</svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="30000" height="30000"></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8"><script>alert(1)</script></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8"><style>@import "file:///etc/passwd";</style></svg>',
    ],
)
def test_svg_refuses_resources_scripts_and_oversize(tmp_path, payload):
    path = tmp_path / "unsafe.svg"
    path.write_text(payload)
    with pytest.raises(ValueError):
        store.import_image(path)


def test_svg_read_is_bounded_even_when_preflight_size_is_stale(tmp_path, monkeypatch):
    path = tmp_path / "replaced.svg"
    path.write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8">'
        + b" " * (MAX_SVG_BYTES + 1)
        + b"</svg>"
    )
    actual_stat = Path.stat

    def stale_stat(candidate, *args, **kwargs):
        if candidate == path:
            return SimpleNamespace(st_size=128)
        return actual_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stale_stat)
    with pytest.raises(ValueError, match="4 MiB import limit"):
        store.import_image(path)


def test_svg_refuses_nested_scene_instead_of_silently_dropping_shapes(tmp_path):
    path = tmp_path / "nested.svg"
    depth = 40  # QtSvg's normal renderer only accepts 32 nested nodes.
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8">'
        + "<g>" * depth
        + '<rect x="1" y="1" width="4" height="4" fill="red"/>'
        + "</g>" * depth
        + "</svg>"
    )
    with pytest.raises(ValueError, match="complexity limit"):
        store.import_image(path)


def test_svg_refuses_excessive_flat_scene_before_qt_rendering(tmp_path):
    path = tmp_path / "many-shapes.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="8">'
        + '<rect x="1" y="1" width="1" height="1"/>' * 10_001
        + "</svg>"
    )
    with pytest.raises(ValueError, match="complexity limit"):
        store.import_image(path)
