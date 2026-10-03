"""Photoshop fixtures based on PSDFixture.swift and PSDReader.swift."""

import struct
import tracemalloc

import pytest
from compositor_linux import psd


def _u16(value):
    return struct.pack(">H", value)


def _u32(value):
    return struct.pack(">I", value)


def _u64(value):
    return struct.pack(">Q", value)


def fixture(
    *, psb=False, group=False, rle=False, unsupported=False, text_extra=None, no_pixels=False
):
    width = height = 2
    size = _u64 if psb else _u32

    def channel(value):
        plane = bytes([value]) * 4
        if rle:
            lengths = (_u32(2) if psb else _u16(2)) * 2
            plane = lengths + bytes([255, value]) * 2
        return _u16(1 if rle else 0) + plane

    def record(name, section=0, blend=b"mul ", alpha=255, mask=False):
        channels = [(-1, channel(alpha)), (0, channel(200)), (1, channel(100)), (2, channel(50))]
        if section or no_pixels:
            channels = []
        if mask:
            channels += [(-2, _u16(0) + bytes([0, 255, 255, 0]))]
        extra = _u32(20 if mask else 0)
        if mask:
            extra += struct.pack(">iiiiBBH", 0, 0, 2, 2, 255, 1, 0)
        name_bytes = name.encode("ascii")
        extra += _u32(0) + bytes([len(name_bytes)]) + name_bytes
        extra += bytes(-(1 + len(name_bytes)) % 4)
        if section:
            payload = _u32(section)
            extra += b"8BIMlsct" + _u32(4) + payload
        if text_extra is not None and not section:
            extra += b"8BIMTySh" + _u32(len(text_extra)) + text_extra
            extra += bytes(len(text_extra) % 2)
        data = struct.pack(">iiiiH", 0, 0, 0 if section else 2, 0 if section else 2, len(channels))
        for id, payload in channels:
            data += struct.pack(">h", id) + size(len(payload))
        data += b"8BIM" + blend + bytes([255, 0, 0, 0]) + _u32(len(extra)) + extra
        return data, b"".join(payload for _, payload in channels)

    records = [record("Pixels", mask=True)]
    if group:
        records = [
            record("Divider", section=3, blend=b"norm"),
            records[0],
            record("Folder", section=1, blend=b"pass"),
        ]
    info = struct.pack(">h", len(records))
    info += b"".join(row[0] for row in records)
    info += b"".join(row[1] for row in records)
    layer_section = size(len(info)) + info + _u32(0)
    header = b"8BPS" + _u16(2 if psb else 1) + bytes(6)
    header += _u16(4) + _u32(height) + _u32(width)
    header += _u16(16 if unsupported else 8) + _u16(3) + _u32(0) + _u32(0)
    return header + size(len(layer_section)) + layer_section + _u16(0) + bytes(16)


def text_block(*, vertical=False, invalid_matrix=False):
    def identifier(value):
        return (
            _u32(0) + value.encode("ascii")
            if len(value) == 4
            else _u32(len(value)) + value.encode("ascii")
        )

    def string(value):
        return _u32(len(value)) + value.encode("utf-16-be")

    def descriptor(name, items):
        return (
            string("")
            + identifier(name)
            + _u32(len(items))
            + b"".join(identifier(key) + kind + payload for key, kind, payload in items)
        )

    engine = (
        b"<< /EngineDict << /StyleRun << /RunArray [ << /StyleSheet << "
        b"/StyleSheetData << /Font 0 /FontSize 24 /Tracking 100 "
        b"/FillColor << /Values [ 1 1 0 0 ] >> >> >> >> ] >> "
        b"/ParagraphRun << /RunArray [ << /ParagraphSheet << /Properties "
        b"<< /Justification 2 >> >> >> ] >> >> "
        b"/ResourceDict << /FontSet [ << /Name (Helvetica) >> ] >> >>"
    )
    items = [
        ("Txt ", b"TEXT", string("Editable")),
        ("Ornt", b"enum", identifier("Ornt") + identifier("Vrtc" if vertical else "Hrzn")),
        ("EngineData", b"tdta", _u32(len(engine)) + engine),
    ]
    matrix = (2.0, 0.0, 0.0, 1.0 if invalid_matrix else 2.0, 40.0, 50.0)
    return (
        _u16(1)
        + struct.pack(">6d", *matrix)
        + _u16(50)
        + _u32(16)
        + descriptor("TxLr", items)
        + _u16(1)
        + _u32(16)
        + descriptor(
            "warp", [("warpStyle", b"enum", identifier("warpStyle") + identifier("warpNone"))]
        )
    )


@pytest.mark.parametrize(
    "psb,rle,group",
    [(False, False, False), (False, True, True), (True, False, True), (True, True, False)],
)
def test_layered_photoshop_import(psb, rle, group):
    result = psd.read(fixture(psb=psb, rle=rle, group=group))
    doc = result.document
    assert (doc.width, doc.height) == (2, 2)
    assert len(doc.layers) == (2 if group else 1)
    pixels = doc.layers[0]
    assert pixels.image.getpixel((0, 0)) == (200, 100, 50, 255)
    assert pixels.mask.getpixel((0, 0)) == 0
    assert not pixels.mask_linked
    assert pixels.blend == "Multiply"
    assert pixels.parent == (doc.layers[1].id if group else None)
    if group:
        assert doc.layers[1].group


def test_unsupported_and_truncated_files_refuse_without_partial_document():
    with pytest.raises(ValueError, match="8-bit RGB"):
        psd.read(fixture(unsupported=True))
    for cut in (0, 20, 80, len(fixture()) - 30):
        with pytest.raises(ValueError):
            psd.read(fixture()[:cut])


def test_bad_packbits_length_refused():
    data = bytearray(fixture(rle=True))
    position = data.find(bytes([0, 2, 0, 2, 255, 255]))
    assert position > 0
    data[position : position + 2] = bytes([255, 255])
    with pytest.raises(ValueError):
        psd.read(data)


def test_merged_only_psd_opens_with_conversion_notice():
    header = (
        b"8BPS"
        + _u16(1)
        + bytes(6)
        + _u16(3)
        + _u32(1)
        + _u32(1)
        + _u16(8)
        + _u16(3)
        + _u32(0)
        + _u32(0)
    )
    result = psd.read(header + _u32(0) + _u16(0) + bytes([10, 20, 30]))
    assert result.document.layers[0].image.getpixel((0, 0)) == (10, 20, 30, 255)
    assert "Merged image only" in result.conversions[0]


def merged_only(*, psb=False, rle=False, channels=56, width=8, height=8):
    size = _u64 if psb else _u32
    header = (
        b"8BPS"
        + _u16(2 if psb else 1)
        + bytes(6)
        + _u16(channels)
        + _u32(height)
        + _u32(width)
        + _u16(8)
        + _u16(3)
        + _u32(0)
        + _u32(0)
        + size(0)
        + _u16(1 if rle else 0)
    )
    if rle:
        assert 1 <= width <= 128
        lengths = (_u32(2) if psb else _u16(2)) * (channels * height)
        rows = b"".join(
            bytes([(1 - width) & 0xFF, channel]) * height for channel in range(channels)
        )
        return header + lengths + rows
    return header + b"".join(bytes([channel]) * (width * height) for channel in range(channels))


@pytest.mark.parametrize("psb", [False, True])
def test_merged_extra_packbits_channels_are_not_decompressed(psb, monkeypatch):
    original = psd._packbits
    decoded = 0

    def count_decodes(*args):
        nonlocal decoded
        decoded += 1
        assert decoded <= 4, "Extra merged channels must not be materialized"
        return original(*args)

    monkeypatch.setattr(psd, "_packbits", count_decodes)
    image = psd.read(merged_only(psb=psb, rle=True)).document.layers[0].image
    assert image is not None
    assert image.getpixel((0, 0)) == (0, 1, 2, 3)
    assert decoded == 4


def test_merged_extra_raw_channels_do_not_exhaust_decoded_memory():
    data = merged_only(width=512, height=512)
    tracemalloc.start()
    try:
        image = psd.read(data).document.layers[0].image
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert image is not None
    assert image.getpixel((0, 0)) == (0, 1, 2, 3)
    assert peak < 8 * 1024 * 1024, f"Unused channels occupied {peak} traced bytes"


@pytest.mark.parametrize("psb,rle", [(False, False), (False, True), (True, False), (True, True)])
def test_merged_unused_channel_payload_must_be_present(psb, rle):
    complete = merged_only(psb=psb, rle=rle)
    with pytest.raises(ValueError, match="damaged|incomplete"):
        psd.read(complete[:-1])


def test_photoshop_text_imports_editable_style_and_preserves_raster():
    imported = psd.read(fixture(text_extra=text_block()))
    layer = imported.document.layers[0]
    assert layer.live_text is not None
    assert layer.live_text.settings["text"] == "Editable"
    assert layer.live_text.settings["size"] == 48
    assert layer.live_text.settings["alignment"] == "Center"
    assert layer.live_text.settings["color"] == [255, 0, 0]
    assert layer.image.getpixel((0, 0)) == (200, 100, 50, 255)


@pytest.mark.parametrize(
    "block", [text_block(vertical=True), text_block(invalid_matrix=True), b"broken"]
)
def test_unrepresentable_photoshop_text_has_reported_raster_fallback(block):
    imported = psd.read(fixture(text_extra=block))
    assert imported.document.layers[0].live_text is None
    assert any("text rasterized" in note for note in imported.conversions)
    with pytest.raises(ValueError, match="raster fallback"):
        psd.read(fixture(text_extra=block, no_pixels=True))
