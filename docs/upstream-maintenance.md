# Maintaining the Linux port

Upstream: [robbietilton/Compositor](https://github.com/robbietilton/Compositor).
Linux fork: [dlpwaters/compositor](https://github.com/dlpwaters/compositor).
The reviewed source baseline is pinned by tag and full commit in
[`upstream.json`](upstream.json). Linux 0.1.1 is based on upstream 1.0.4;
Linux release numbers and upstream release numbers are independent.

## Automatic checks

The **Upstream releases** workflow checks the latest published stable release
daily at **14:23 UTC**. It also runs when its configuration or implementation
changes, and supports manual dispatch. GitHub schedules activate only after
the workflow reaches the repository's default branch.

The watcher resolves the release tag to a commit, fetches history into a temporary
bare repository, and compares complete Git trees. It avoids GitHub's 300-file
comparison API limit and groups changed paths into shared C kernels, project
format, editing/rendering, macOS integration, and other files. These categories
guide review; they do not establish whether a change is safe or relevant.
Renames appear as a deletion and an addition. A divergent baseline, failed API
request or failed fetch makes the workflow fail instead of reporting no updates.

Each successful run includes a readable job summary and a 30-day artifact with
`report.md` and the complete `report.json`. Trusted default-branch runs update
[issue #3](https://github.com/dlpwaters/compositor/issues/3), titled
**Upstream releases: Linux port review**. Its repository and number are pinned
in `upstream.json` to avoid issue-list discovery races. Other
branches and pull requests only produce reports. The publishing job has issue
write permission; the report job has read permission. No external service or
additional credential is required in Actions.

The issue changes only when its content changes. Check workflow run times for
freshness, not the issue's last-updated time. Closed issues are still recognized
and updated without creating duplicates or reopening them; keep the tracking
issue open while using it as the review queue. An unavailable issue, missing
watcher marker, or mismatched fork causes a failure instead of creating or
overwriting another issue. To reuse this setup in another fork, create its
tracking issue with the watcher marker and configure that repository/number.

The **Linux** workflow also runs each Monday at **13:41 UTC**. It tests Python
3.12 and 3.14, lint/format, distributions, the desktop entry, and a fresh
ordinary-user install/reinstall/removal in Arch. Dependency versions are
resolved afresh within the project's declared ranges. This can detect Linux
dependency breakage even when upstream has not released anything.

Scheduled Actions can be delayed, and GitHub disables public-repository
schedules after 60 days without repository activity. The daily Hermes check
should detect disabled or stale monitoring. These checks are headless; native
Wayland editing and Mac/Linux reference comparisons remain separate acceptance
steps. No installed editor or saved project is updated by these workflows.

## Run a report locally

Use Python 3.11+, Git and GitHub CLI with the existing keyring login. Public
upstream reads can also use GitHub CLI's authenticated Actions environment.

```sh
python scripts/upstream-check.py --output /tmp/compositor-upstream-report
```

This writes only report files and a temporary Git repository, which is removed
when the check finishes. It does not modify checkout branches or add a remote.
Publishing an issue is a separate, explicit option:

```sh
python scripts/upstream-check.py \
  --publish-report /tmp/compositor-upstream-report/report.md \
  --publish-issue dlpwaters/compositor
```

## Review and integrate an update

1. Inspect the tracking issue and full comparison. Prioritize project-format
   changes, data loss fixes, security fixes, shared C signatures, and rendering
   regressions. Distinguish relevant fixes from new optional Mac features.
2. Work on a feature branch. Review each shared C change against the Linux
   bindings before importing it. Translate relevant Swift/Metal behavior into
   the Python/Qt implementation; merging Mac sources alone does not port it.
3. Add regressions for changed behavior and project metadata. Run Linux CI,
   affected native Wayland workflows, and Mac reference comparisons where
   project interchange or rendered output changed. Test with project copies.
4. Document applied changes, intentionally excluded changes, and remaining
   qualification in the review PR. Advance the pinned baseline only after
   every relevant change through that commit has been accounted for. Merely
   fetching a tag or reading the report does not advance the baseline.
5. Review and merge through the repository's normal approval process. Publish
   a Linux release separately after its acceptance checks; retain the previous
   qualified release for rollback.

Initial live review found upstream **v1.4.5** at
`086f1631573ccb2b57644e53b52bf1488fc976aa`: **316 commits, 207 changed paths,
10 shared C paths**, and a project-format advance from **7 to 11**. The Linux
reader still accepts only versions 1–7. This is a compatibility review priority;
the monitoring setup does not implement format 11 or upgrade the editor.

Give [`hermes-daily-prompt.md`](hermes-daily-prompt.md) to Hermes to create the
daily read-only review and actionable digest.
