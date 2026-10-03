"""Release monitoring: complete diffs, compatibility signals and idempotent issues."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("upstream_check", ROOT / "scripts/upstream-check.py")
watcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watcher)


@pytest.fixture
def history(tmp_path):
    watcher.command("git", "init", "--quiet", str(tmp_path))

    def write(path, content):
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)

    def commit():
        watcher.command("git", "add", ".", cwd=tmp_path)
        watcher.command(
            "git",
            "-c",
            "user.name=Monitor Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
            cwd=tmp_path,
        )
        return watcher.command("git", "rev-parse", "HEAD", cwd=tmp_path).strip()

    write("README.md", "root")
    parent = commit()
    write("Compositor/IO/ProjectStore.swift", "var version = 7\n")
    write("Compositor/Rendering/HealPixels.c", "original")
    write("old asset.png", "old")
    baseline = commit()
    write(
        "Compositor/IO/ProjectStore.swift",
        "static let current = 11\nvar version = ProjectManifest.current\n",
    )
    write("Compositor/Rendering/HealPixels.c", "changed")
    write("Compositor/UI/TypeTool.swift", "new UI")
    (tmp_path / "old asset.png").unlink()
    for index in range(305):
        write(f"assets/file-{index}.txt", "new")
    latest = commit()
    return tmp_path, parent, baseline, latest, write, commit


def test_complete_tree_diff_and_project_format_detection(history):
    repository, _, baseline, latest, _, _ = history
    result = watcher.collect_changes(repository, baseline, latest)
    assert result["commits_ahead"] == 1
    assert len(result["files"]) == 309  # Includes files beyond GitHub's 300-file compare limit.
    assert result["project_format"] == {"baseline": 7, "latest": 11}
    assert {item["path"]: item["status"] for item in result["files"]}["old asset.png"] == "D"
    assert {item["path"]: item["category"] for item in result["files"]}[
        "Compositor/Rendering/HealPixels.c"
    ] == "shared_kernels"


def test_baseline_with_no_new_release_changes(history):
    repository, _, baseline, _, _, _ = history
    result = watcher.collect_changes(repository, baseline, baseline)
    assert result == {
        "files": [],
        "commits_ahead": 0,
        "project_format": {"baseline": 7, "latest": 7},
    }


def test_divergent_release_is_not_reported_as_a_safe_update(history):
    repository, parent, baseline, _, write, commit = history
    watcher.command("git", "checkout", "--quiet", "-b", "diverged", parent, cwd=repository)
    write("other.txt", "divergent history")
    divergent = commit()
    with pytest.raises(ValueError, match="diverged"):
        watcher.collect_changes(repository, baseline, divergent)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("Compositor/Rendering/LevelsPixels.h", "shared_kernels"),
        ("Compositor/IO/PSDReader.swift", "project_format"),
        ("docs/project-format.md", "project_format"),
        ("Compositor/Rendering/EditorCanvas.swift", "porting"),
        ("Compositor/Document/LayerAdjustment.swift", "porting"),
        ("Compositor/ContentView.swift", "porting"),
        ("Compositor.xcodeproj/project.pbxproj", "macos"),
        ("scripts/release.sh", "macos"),
        ("README.md", "other"),
    ],
)
def test_classification(path, expected):
    assert watcher.category(path) == expected


def report_fixture():
    return {
        "checked_at": "2026-10-03T00:00:00Z",
        "repository": "robbietilton/Compositor",
        "baseline_tag": "v1.0.4",
        "baseline_commit": "a" * 40,
        "latest_tag": "v1.4.5",
        "latest_commit": "b" * 40,
        "published_at": "2026-09-29T21:42:56Z",
        "release_url": "https://github.com/robbietilton/Compositor/releases/tag/v1.4.5",
        "compare_url": "https://github.com/robbietilton/Compositor/compare/base...latest",
        "release_notes": "Fix curves.\n```\n@someone <instructions>\n```",
        "commits_ahead": 1,
        "files": [
            {
                "status": "M",
                "path": "Compositor/IO/ProjectStore.swift",
                "category": "project_format",
            }
        ],
        "project_format": {"baseline": 7, "latest": 11},
    }


def test_report_is_stable_but_detects_release_note_edits():
    report = report_fixture()
    first = watcher.render_report(report)
    assert "Project format advanced from 7 to 11" in first
    assert "````\nFix curves." in first  # Upstream backticks cannot escape the quoted notes.
    report["checked_at"] = "2026-10-04T00:00:00Z"
    assert watcher.render_report(report) == first
    report["release_notes"] = "Updated release notes"
    assert watcher.render_report(report) != first


def test_unknown_schema_is_visible_instead_of_assumed_compatible():
    report = report_fixture()
    report["project_format"]["latest"] = None
    assert "could not be determined" in watcher.render_report(report)


def test_unchanged_closed_issue_is_not_duplicated_or_reopened(monkeypatch):
    body = watcher.render_report(report_fixture())
    calls = []

    def api(endpoint, **arguments):
        calls.append((endpoint, arguments))
        return {"number": 3, "body": body, "state": "closed", "html_url": "https://example.com/3"}

    monkeypatch.setattr(watcher, "api", api)
    tracking = {"repository": "dlpwaters/compositor", "number": 3}
    assert watcher.publish_issue("dlpwaters/compositor", body, tracking).startswith("Unchanged:")
    assert len(calls) == 1


def test_pinned_issue_updates_directly_without_list_discovery(monkeypatch):
    calls = []

    def api(endpoint, **arguments):
        calls.append((endpoint, arguments))
        if not arguments:
            return {
                "number": 3,
                "body": watcher.ISSUE_MARKER + "\nold",
                "html_url": "https://example.com/3",
            }
        return {}

    monkeypatch.setattr(watcher, "api", api)
    watcher.publish_issue(
        "dlpwaters/compositor", "new report", {"repository": "dlpwaters/compositor", "number": 3}
    )
    assert len(calls) == 2
    assert calls[0] == ("repos/dlpwaters/compositor/issues/3", {})
    assert calls[-1] == (
        "repos/dlpwaters/compositor/issues/3",
        {"method": "PATCH", "payload": {"body": "new report"}},
    )


def test_wrong_issue_or_fork_fails_without_creating_or_overwriting(monkeypatch):
    calls = []

    def api(endpoint, **arguments):
        calls.append((endpoint, arguments))
        return {"body": "Unrelated user issue"}

    monkeypatch.setattr(watcher, "api", api)
    tracking = {"repository": "dlpwaters/compositor", "number": 3}
    with pytest.raises(ValueError, match="not the upstream"):
        watcher.publish_issue("dlpwaters/compositor", "new report", tracking)
    with pytest.raises(ValueError, match="different fork"):
        watcher.publish_issue("someone/compositor", "new report", tracking)
    assert len(calls) == 1 and calls[0][1] == {}


def test_config_rejects_non_sha_baselines(tmp_path):
    path = tmp_path / "upstream.json"
    path.write_text(
        json.dumps(
            {
                "repository": "robbietilton/Compositor",
                "baseline_commit": "main",
                "baseline_tag": "v1",
            }
        )
    )
    with pytest.raises(ValueError, match="commit SHA"):
        watcher.load_config(path)


def test_release_api_failure_stops_before_fetching_or_publishing(monkeypatch):
    def fail(*arguments, **kwargs):
        raise RuntimeError("GitHub unavailable")

    monkeypatch.setattr(watcher, "api", fail)
    with pytest.raises(RuntimeError, match="GitHub unavailable"):
        watcher.inspect_release(watcher.load_config(ROOT / "docs/upstream.json"))
