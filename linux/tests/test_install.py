"""Installer boundaries: read-only preflight and preservation of unrelated files."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def installer(tmp_path, *arguments, missing=False):
    commands = tmp_path / "commands"
    commands.mkdir()
    for name, body in {
        "pacman": "exit 1" if missing else "exit 0",
        "uv": 'echo "unexpected install" >&2; exit 91',
        "pkexec": 'echo "unexpected elevation" >&2; exit 92',
        "cc": "exit 0",
        "desktop-file-validate": "exit 0",
    }.items():
        script = commands / name
        script.write_text(f"#!/bin/sh\n{body}\n")
        script.chmod(0o755)
    return subprocess.run(
        ["bash", str(ROOT / "install.sh"), *arguments],
        env=dict(os.environ, PATH=f"{commands}:{os.environ['PATH']}"),
        text=True,
        capture_output=True,
        check=False,
    )


def test_preflight_never_installs_or_elevates(tmp_path):
    result = installer(tmp_path, "--check", "--install-system-deps")
    assert result.returncode == 0, result.stderr
    assert "Prerequisites OK" in result.stdout
    assert "unexpected" not in result.stderr


def test_missing_packages_preflight_fails_without_elevation(tmp_path):
    result = installer(tmp_path, "--check", "--install-system-deps", missing=True)
    assert result.returncode == 1
    assert "Missing Arch prerequisites" in result.stdout
    assert "unexpected" not in result.stderr


@pytest.mark.parametrize(
    "arguments", [("--install-model", "--without-background"), ("--remove", "--check")]
)
def test_contradictory_options_fail_before_install(tmp_path, arguments):
    result = installer(tmp_path, *arguments)
    assert result.returncode == 1
    assert "unexpected" not in result.stderr


@pytest.fixture
def desktop_installer(tmp_path, monkeypatch):
    root = tmp_path / "checkout with spaces"
    home = tmp_path / "user with spaces"
    for relative in (
        "scripts/linux-desktop.py",
        "packaging/linux/compositor.desktop",
        "linux/compositor_linux/assets/compositor.png",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    executable = root / "linux/.venv/bin/compositor-linux"
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    executable.chmod(0o755)
    spec = importlib.util.spec_from_file_location(
        "desktop_installer", root / "scripts/linux-desktop.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    monkeypatch.setattr(module.shutil, "which", lambda name: None)

    def run(action):
        monkeypatch.setattr(sys, "argv", ["linux-desktop.py", action])
        module.main()

    return home, root, run


def test_launchers_handle_spaces_reinstall_and_preserve_projects(desktop_installer):
    home, root, run = desktop_installer
    run("install")
    run("install")
    launcher = home / ".local/bin/compositor-linux"
    result = subprocess.run(
        [str(launcher), "image with spaces.png"], capture_output=True, text=True
    )
    assert result.returncode == 0 and result.stdout.strip() == "image with spaces.png"
    project = home / "Keep.comp"
    project.mkdir()
    (project / "manifest.json").write_text("keep")
    run("remove")
    assert not launcher.exists()
    assert (project / "manifest.json").read_text() == "keep"
    assert (root / "linux/.venv/bin/compositor-linux").exists()


def test_unrelated_compatibility_command_is_preserved(desktop_installer):
    home, _, run = desktop_installer
    alias = home / ".local/bin/compositor"
    alias.parent.mkdir(parents=True)
    alias.write_text("another application")
    run("install")
    assert (alias.parent / "compositor-linux").exists()
    run("remove")
    assert alias.read_text() == "another application"


def test_conflicting_primary_launcher_refuses_before_writes(desktop_installer):
    home, _, run = desktop_installer
    launcher = home / ".local/bin/compositor-linux"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("unrelated")
    with pytest.raises(SystemExit, match="Preserving existing file"):
        run("install")
    assert launcher.read_text() == "unrelated"
    assert not (home / "data/applications/compositor.desktop").exists()


def test_removal_preserves_symlink_even_to_managed_file(desktop_installer):
    home, _, run = desktop_installer
    run("install")
    alias = home / ".local/bin/compositor"
    alias.unlink()
    target = home / "another-managed-file"
    target.write_text("# Managed by Compositor's user installer")
    alias.symlink_to(target)
    run("remove")
    assert alias.is_symlink() and target.exists()
