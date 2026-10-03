#!/usr/bin/env python3
"""Report upstream release changes without modifying the Linux checkout."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
ISSUE_MARKER = "<!-- compositor-upstream-report:v1 -->"
CATEGORIES = {
    "shared_kernels": "Shared C kernels — rebuild and test the Linux bindings",
    "project_format": "Project format — review compatibility before importing newer projects",
    "porting": "Editing and rendering — translate relevant changes into Python/Qt",
    "macos": "macOS integration — review for indirect effects; usually platform-specific",
    "other": "Other files — inspect documentation, assets, tests and build changes",
}


def command(*arguments, cwd=None, input_text=None):
    result = subprocess.run(
        arguments, cwd=cwd, input=input_text, capture_output=True, text=True, timeout=180
    )
    if result.returncode:
        raise RuntimeError(f"{arguments[0]} failed: {result.stderr.strip()[:1200]}")
    return result.stdout


def api(endpoint, *, method="GET", payload=None):
    arguments = ["gh", "api", endpoint, "--method", method]
    if payload is not None:
        arguments += ["--input", "-"]
    return json.loads(
        command(*arguments, input_text=json.dumps(payload) if payload is not None else None)
    )


def repository_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", value):
        raise ValueError("Repository must be an owner/name pair.")
    return value


def commit_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Expected a full, lowercase Git commit SHA.")
    return value


def load_config(path):
    config = json.loads(path.read_text())
    repository_name(config["repository"])
    commit_sha(config["baseline_commit"])
    if not isinstance(config["baseline_tag"], str) or not config["baseline_tag"]:
        raise ValueError("The reviewed baseline needs a tag label.")
    tracking = config["tracking_issue"]
    repository_name(tracking["repository"])
    if type(tracking["number"]) is not int or tracking["number"] < 1:
        raise ValueError("Tracking issue number must be a positive integer.")
    return config


def category(path):
    if path.startswith("Compositor/Rendering/") and path.endswith((".c", ".h")):
        return "shared_kernels"
    if path.startswith("Compositor/IO/") or path == "docs/project-format.md":
        return "project_format"
    if path.startswith(("Compositor/Document/", "Compositor/Rendering/", "Compositor/UI/")):
        return "porting"
    if path in ("Compositor/ContentView.swift", "Compositor/CompositorApp.swift"):
        return "porting"
    if path.startswith(("Compositor/", "Compositor.xcodeproj/")) or path in (
        "appcast.xml",
        "scripts/publish.sh",
        "scripts/release.sh",
    ):
        return "macos"
    return "other"


def project_version(repository, commit):
    path = "Compositor/IO/ProjectStore.swift"
    if not command("git", "ls-tree", "--name-only", commit, "--", path, cwd=repository).strip():
        return None
    source = command("git", "show", f"{commit}:{path}", cwd=repository)
    match = re.search(r"(?:static\s+let\s+current|var\s+version)\s*=\s*(\d+)", source)
    return int(match[1]) if match else None


def collect_changes(repository, baseline, latest):
    ancestor = command("git", "merge-base", baseline, latest, cwd=repository).strip()
    if ancestor != baseline:
        raise ValueError("Upstream history diverged from the baseline; inspect it manually.")
    # A tree diff avoids GitHub's 300-file compare limit and does not download image blobs.
    fields = command(
        "git", "diff", "--name-status", "--no-renames", "-z", baseline, latest, cwd=repository
    ).split("\0")
    files = []
    for index in range(0, len(fields) - 1, 2):
        status, path = fields[index : index + 2]
        files.append({"status": status, "path": path, "category": category(path)})
    return {
        "files": files,
        "commits_ahead": int(
            command("git", "rev-list", "--count", f"{baseline}..{latest}", cwd=repository)
        ),
        "project_format": {
            "baseline": project_version(repository, baseline),
            "latest": project_version(repository, latest),
        },
    }


def inspect_release(config):
    upstream = config["repository"]
    release = api(f"repos/{upstream}/releases/latest")
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("Expected a published stable upstream release.")
    tag = release["tag_name"]
    if not isinstance(tag, str) or not tag:
        raise ValueError("Upstream release has no tag.")
    latest = commit_sha(api(f"repos/{upstream}/commits/{quote(tag, safe='')}")["sha"])
    baseline = config["baseline_commit"]
    with tempfile.TemporaryDirectory(prefix="compositor-upstream-") as directory:
        repository = Path(directory)
        command("git", "init", "--bare", "--quiet", str(repository))
        command(
            "git", "remote", "add", "origin", f"https://github.com/{upstream}.git", cwd=repository
        )
        command("git", "config", "remote.origin.promisor", "true", cwd=repository)
        command("git", "config", "remote.origin.partialclonefilter", "blob:none", cwd=repository)
        command(
            "git",
            "fetch",
            "--quiet",
            "--no-tags",
            "--filter=blob:none",
            "origin",
            baseline,
            latest,
            cwd=repository,
        )
        changes = collect_changes(repository, baseline, latest)
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "repository": upstream,
        "baseline_tag": config["baseline_tag"],
        "baseline_commit": baseline,
        "latest_tag": tag,
        "latest_commit": latest,
        "release_url": f"https://github.com/{upstream}/releases/tag/{quote(tag, safe='')}",
        "published_at": release["published_at"],
        "release_notes": release.get("body") or "No release notes provided.",
        "compare_url": f"https://github.com/{upstream}/compare/{baseline}...{latest}",
        **changes,
    }


def code(value):
    value = str(value).replace("\n", " ").replace("\r", " ")
    fence = "`" * (max((len(m[0]) for m in re.finditer(r"`+", value)), default=0) + 1)
    return f"{fence} {value} {fence}"


def render_report(report):
    lines = [
        ISSUE_MARKER,
        "# Upstream release review",
        "",
        f"Reviewed Linux baseline: **{code(report['baseline_tag'])}** "
        f"({code(report['baseline_commit'])}).",
        f"Latest stable upstream release: **{code(report['latest_tag'])}** "
        f"({code(report['latest_commit'])}), published {report['published_at']}.",
        f"[Release notes]({report['release_url']}) · [Full comparison]({report['compare_url']})",
        "",
        f"**{report['commits_ahead']} commits and {len(report['files'])} changed paths** "
        "since the reviewed baseline. This reports source changes; it does not certify Linux parity.",
        "",
        "## Priority actions",
        "",
    ]
    versions = report["project_format"]
    if versions["baseline"] is None or versions["latest"] is None:
        lines += [
            "- Project-format version could not be determined. Inspect ProjectStore before assuming compatibility."
        ]
    elif versions["latest"] > versions["baseline"]:
        lines += [
            f"- **Project format advanced from {versions['baseline']} to {versions['latest']}.** "
            "Check the Linux reader's supported versions and newer metadata before opening Mac projects. "
            "Keep original projects; qualify Mac/Linux round-trips on copies.",
        ]
    groups = {name: [f for f in report["files"] if f["category"] == name] for name in CATEGORIES}
    if groups["shared_kernels"]:
        lines += [
            "- Review changed C signatures and semantics against linux/compositor_linux/kernels.py; rebuild and run pixel-operation regressions."
        ]
    if groups["porting"]:
        lines += [
            "- Translate relevant Swift editing/rendering fixes into Python/Qt. A source merge alone does not implement them on Linux."
        ]
    if report["commits_ahead"]:
        lines += [
            "- Prepare a scoped update PR; run Linux CI, affected native Wayland workflows and appropriate reference comparisons before releasing."
        ]
    else:
        lines += [
            "- No new stable-release source changes since the reviewed baseline. Continue checking Linux CI and dependencies."
        ]
    lines += [
        "- Advance docs/upstream.json only after every relevant change has been applied or its exclusion documented in the review PR.",
        "",
        "## Latest release notes (upstream text)",
        "",
    ]
    notes = report["release_notes"][:8000]
    fence = "`" * max(3, max((len(m[0]) for m in re.finditer(r"`+", notes)), default=0) + 1)
    lines += [fence, notes, fence, ""]
    if len(report["release_notes"]) > 8000:
        lines += ["Release notes truncated here; follow the release link for the full text.", ""]
    for name, title in CATEGORIES.items():
        files = groups[name]
        if not files:
            continue
        lines += [f"## {title} ({len(files)} paths)", ""]
        lines += [f"- {item['status']}: {code(item['path'])}" for item in files[:30]]
        if len(files) > 30:
            lines += [
                f"- {len(files) - 30} more paths; see the full comparison and report.json artifact."
            ]
        lines += [""]
    lines += [
        "## Review rules",
        "",
        "File grouping is a path-based starting point, not a semantic safety decision. "
        "No sources, baseline, installed editor, projects or release tags are changed by this watcher. "
        "No automatic merges or releases are performed. "
        "The issue changes only when report content changes; workflow run times establish freshness.",
    ]
    body = "\n".join(lines) + "\n"
    fingerprint = hashlib.sha256(body.encode()).hexdigest()
    return body + f"\n<!-- report-sha256:{fingerprint} -->\n"


def publish_issue(repository, body, tracking):
    repository_name(repository)
    if repository != tracking["repository"]:
        raise ValueError("Tracking issue belongs to a different fork; update docs/upstream.json.")
    endpoint = f"repos/{repository}/issues/{tracking['number']}"
    issue = api(endpoint)
    if issue.get("pull_request") or not (issue.get("body") or "").startswith(ISSUE_MARKER + "\n"):
        raise ValueError(
            "Configured issue is not the upstream tracking issue; inspect it manually."
        )
    if issue.get("body") == body:
        return f"Unchanged: {issue['html_url']}"
    api(endpoint, method="PATCH", payload={"body": body})
    return f"Updated: {issue['html_url']}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "docs/upstream.json")
    parser.add_argument("--output", type=Path, default=Path("/tmp/compositor-upstream-report"))
    parser.add_argument(
        "--publish-issue",
        metavar="OWNER/REPO",
        help="Explicitly update the configured tracking issue",
    )
    parser.add_argument(
        "--publish-report",
        type=Path,
        help="Publish an existing report.md instead of fetching upstream",
    )
    args = parser.parse_args()
    if args.publish_report and not args.publish_issue:
        parser.error("--publish-report requires --publish-issue")
    try:
        if args.publish_report:
            body = args.publish_report.read_text()
            if not body.startswith(ISSUE_MARKER + "\n"):
                raise ValueError("Not an upstream watcher report.")
        else:
            report = inspect_release(load_config(args.config))
            body = render_report(report)
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            (args.output / "report.md").write_text(body)
            print(
                f"{report['baseline_tag']} -> {report['latest_tag']}: "
                f"{len(report['files'])} changed paths; report: {args.output / 'report.md'}"
            )
        if args.publish_issue:
            print(
                publish_issue(args.publish_issue, body, load_config(args.config)["tracking_issue"])
            )
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Upstream check failed: {error}\n")


if __name__ == "__main__":
    main()
