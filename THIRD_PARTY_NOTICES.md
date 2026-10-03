# Credits and third-party notices

## Compositor

This fork is based on [Robbie Tilton's Compositor](https://github.com/robbietilton/Compositor),
baseline 1.4.5 (`11d8d7a50992b24fd9a760a1c13b1c01b70aaf30`). Copyright (c) 2026
Wonder Assembly LLC. The complete upstream MIT notice is retained in [LICENSE](LICENSE).
The macOS sources, shared C pixel kernels, and application artwork come
from that project. New Linux tool/action SVGs, screenshots, the demo footage and
the synthetic editable sample were made for this fork and use its MIT license.
The sample refers to the separately installed Liberation Sans font; font files
are not redistributed in the sample or application packages.
Both builds compile the shared upstream DitherPixels.c. A platform guard retains
Apple's dispatch scheduling on macOS and selects serial plain-C rows on Linux;
the pixel calculations are shared, not maintained as a separate copy.

## Installed dependencies

The Linux wheel and Arch package contain this application's code and kernel
library. Python/Qt dependencies are installed separately, rather than copied
into a frozen executable. Their licenses and notices remain with their packages.
Use the license files for the actual installed versions when redistributing them.

| Dependency | Source and license reference |
| --- | --- |
| Qt / PySide6 / Shiboken | [Qt for Python licensing](https://doc.qt.io/qtforpython-6/commercial/index.html): community LGPLv3/GPLv3 or commercial; [Qt LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations) |
| NumPy | [BSD-3-Clause and bundled notices](https://github.com/numpy/numpy/blob/main/LICENSE.txt) |
| SciPy | [BSD-3-Clause and bundled notices](https://github.com/scipy/scipy/blob/main/LICENSE.txt) |
| Pillow | [HPND-style license](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| pillow-heif | [BSD-3-Clause](https://github.com/bigcat88/pillow_heif/blob/master/LICENSE.txt); libheif and its codecs have separate licenses |
| libheif | [LGPL-3.0](https://github.com/strukturag/libheif/blob/master/COPYING) |
| ONNX Runtime (optional) | [MIT and third-party notices](https://github.com/microsoft/onnxruntime/blob/main/LICENSE) |
| rawpy / LibRaw (optional camera RAW decoder) | [rawpy MIT license](https://github.com/letmaik/rawpy/blob/main/LICENSE) and [LibRaw LGPL-2.1 or CDDL-1.0 terms](https://www.libraw.org/about) |

The application uses Qt Core, Gui, Widgets, Svg, and Test for development checks.
It neither modifies Qt/PySide6 nor prevents users from replacing those libraries.
Source code and build instructions are available in this repository. Distributing
a bundle that includes Qt or codecs requires checking those components' notices
and obligations; the application's MIT license does not cover those libraries.

## Optional background model

The explicit installer fetches `u2netp.onnx` from the
[rembg model release](https://github.com/danielgatis/rembg/releases/tag/v0.0.0).
The model is based on [U²-Net](https://github.com/xuebinqin/U-2-Net), whose upstream
code is Apache-2.0. This is source provenance, not a claim that every externally
hosted weight has an independently verified license grant. Weights are excluded
from this repository, wheels, source archives, and Arch packages. Review the
provider's terms before separately redistributing weights. The download checksum
and inference substitution are recorded in the [parity audit](docs/linux-parity.md).
