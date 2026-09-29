from pathlib import Path

from setuptools import Extension, setup

root = Path("Compositor/Rendering")
sources = sorted(str(p) for p in root.glob("*.c"))
# ctypes loads the shared library; these are the original Mac app's C kernels.
setup(
    ext_modules=[
        Extension(
            "compositor_linux._pixels",
            sources,
            include_dirs=[str(root)],
            libraries=["m"],
            define_macros=[("_DEFAULT_SOURCE", "1")],
            extra_compile_args=["-O3", "-std=c11"],
        )
    ]
)
