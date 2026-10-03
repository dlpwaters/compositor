"""Compile and exercise the portable Dither C ABI without Apple blocks or dispatch."""

import os
import shutil
import subprocess
from pathlib import Path

PROBE = r"""
#include "DitherPixels.h"
#include <assert.h>
#include <string.h>

int main(void) {
    uint8_t pixels[12] = {0, 0, 0, 255, 255, 255, 255, 255, 0, 0, 0, 0};
    DitherParams settings = {0};
    settings.style = DITHER_ATKINSON;
    settings.levels = 2;
    settings.diffusion = 1;
    settings.cell = 4;
    memset(settings.light, 255, sizeof(settings.light));
    assert(dither_apply(pixels, 3, 1, 12, &settings) == 1);
    assert(pixels[0] == 0 && pixels[4] == 255 && pixels[7] == 255);
    assert(pixels[11] == 0);

    uint8_t base[8] = {60, 40, 20, 128, 0, 0, 0, 0};
    const uint8_t glow[8] = {80, 40, 20, 128, 1, 2, 3, 4};
    // Upstream multiplies premultiplied glow by the destination alpha again.
    const uint8_t expected[8] = {80, 50, 25, 128, 0, 0, 0, 0};
    dither_glow(base, glow, 2, 1, 8, 0.5f);
    assert(memcmp(base, expected, 8) == 0);
    dither_dots(base, 2, 1, 8, 1, settings.dark);
    assert(memcmp(base, expected, 8) == 0);

    settings.style = DITHER_SCANLINES;
    settings.originalColors = 1;
    assert(dither_apply(pixels, 3, 1, 12, &settings) == 1);
    assert(pixels[3] == 255 && pixels[7] == 255 && pixels[11] == 0);
    return 0;
}
"""


def test_dither_builds_as_plain_c_and_preserves_alpha(tmp_path):
    compiler = shutil.which(os.environ.get("CC", "cc"))
    assert compiler is not None, "A C compiler is required by the native extension build."
    root = Path(__file__).resolve().parents[2]
    source = root / "Compositor/Rendering/DitherPixels.c"
    probe = tmp_path / "dither_probe.c"
    probe.write_text(PROBE)
    executable = tmp_path / "dither_probe"
    result = subprocess.run(
        [
            compiler,
            "-std=c11",
            "-U__APPLE__",
            "-I" + str(root / "Compositor/Rendering"),
            str(source),
            str(probe),
            "-lm",
            "-o",
            str(executable),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    subprocess.run([str(executable)], check=True, timeout=30)
