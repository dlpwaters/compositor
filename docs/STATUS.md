# Compositor Linux status

## Upstream 1.4.5 integration acceptance — 2026-10-03

The maintainer accepted the measured Motion Blur and smaller rendering differences
and authorized push/PR/merge after final checks. This is a functional Qt/CPU Linux
port, not a claim of pixel-identical Mac rendering; see `linux-parity.md`.

- Target: upstream `11d8d7a50992b24fd9a760a1c13b1c01b70aaf30`, including
  stable v1.4.5 `086f1631573ccb2b57644e53b52bf1488fc976aa`.
- Integration branch: `port/upstream-v1.4.5`; the original checkout is preserved.
- Qualified runtime snapshot: 316 Ubuntu ARM64 tests; 5 native Weston/Wayland
  checks; Arch x86_64 installer, package build/check and installed package smoke.
  macOS core/window suites pass 512/7 tests. All 68 Mac projects load, render,
  save and reopen on Linux; both tested PSD/PSB and Hard Mix outputs are exact.
- Independent archive review found no in-scope security/correctness regression
  in Hard Mix, folder opacity, PSD or SVG. The parent separately exercised the
  approval-blocked SVG limits and all 3 adversarial tests passed.
- The three real Mac renderer fixtures now ship with the tests, so CI/package
  checks need not skip them. Final staged-tree checks and remote CI remain
  required before using the authorized merge; no tagged release is requested.
- Runtime receipts and detailed measurements remain under ignored
  `.runtime/upstream-port/`; the PR and GitHub checks provide the remote receipt.
- Upstream monitoring advances to v1.4.5; Apple-only integration, exact private
  Core Image kernels and broad physical Omarchy desktop acceptance are excluded.

## Historical integration log — superseded checkpoints, not current blockers

All statements of “Current,” “Next,” “HOLD,” or “no commit/push/merge” below
describe their **older checkpoint only**. They are not the current acceptance
criteria. The maintainer subsequently accepted the measured rendering differences
as documented above; the final source and CI gates still apply. For present
publication state, use this top section and the branch's GitHub PR/checks.

Goal: port every Linux-relevant change and feature through upstream `11d8d7a50992b24fd9a760a1c13b1c01b70aaf30` (stable v1.4.5 `086f1631573ccb2b57644e53b52bf1488fc976aa`), preserve Linux behavior, test, and push/merge only after required checks pass. Branch: `port/upstream-v1.4.5`; worktree: `compositor-upstream-port`; fork base: `8aa7a1167464d7d5f9b9c26e8c480a66942834f1`.

The source merge is staged and uncommitted. README keeps the Linux installer/guide; project-format reference retains the Linux text extension and labels released-reader limits. No push or merge to default branch has occurred. The original checkout remains unchanged. Upstream monitoring baseline stays pinned to 1.0.4 until every relevant change is accounted for.

Linux qualification checkpoint (2026-10-03): baseline all 156 tests pass in the isolated Ubuntu 24.04 ARM64 VM after installing actual Qt runtime libraries; the native Weston/Wayland baseline demo passes all five user-path checks. Upstream Mac build-for-testing passes, and its parallel test-result summary reports 510 tests (571 parameterized cases), zero failures. The inherited native SliderSnap regression exposed uninitialized AppKit track geometry; forcing the test window's first display fixes it without changing production code, and the focused retry passes.

The real Mac store/exporter generated 38 synthetic v11 packages and PNG references. A native Linux candidate preview imports/renders/saves/reopens all 38 without error, but pixel deltas still require qualification (especially Hard Mix and CoreImage blur equivalents). These are observations, not a blanket parity claim. New Dither C source initially failed Linux compilation due to Apple dispatch/Blocks; a plain-C regression failed before and passes after a guarded serial-band fallback, retaining Apple's existing parallel path. The candidate preview now builds its native extension.

Earlier Linux candidate diagnostics found stale text-fixture metadata and order-dependent modal callbacks. The UI correction binds only the visible dialog, bounds callbacks, and preserves cancellation/undo assertions. Camera Raw now accepts only correctly typed shared dialog metadata; malformed kinds/preview flags still fail validation. A checksum-verified Ubuntu 24.04 ARM64 source snapshot (`candidate-final-fix2.tar`) installs and passes 298 tests with 2 Mac-fixture-dependent skips. Ruff lint/format and shell syntax pass. A fresh native Weston/Wayland candidate demo exercised five UI/save paths and passed. Native Omarchy desktop acceptance remains unavailable because that tailnet host is offline.

Former worker checkpoint: the broad Codex worker was interrupted (exit receipt 1); its two bounded, disjoint renderer/UI correction jobs completed (exit receipts 0). Their scoped checks passed, and the parent independently reran the native Linux matrix, Wayland UI demo, Mac reference comparisons, and static checks. The single upstream Dither C source is now compiled directly; a redundant Linux wrapper/package copy was removed, and the direct plain-C portability regression passed on macOS. An independent early read-only audit found no confirmed security blocker but explicitly held release for source/render fidelity. All edits remain uncommitted in the integration worktree; no branch push or default-branch merge has occurred.

Expanded cross-platform evidence: the 68 real Mac-generated v11 projects all load/render/save/reopen on the new checksum-verified Ubuntu candidate; 25 are byte-exact against Mac export. Transparent Hard Mix's maximum channel difference fell from 141 to 2 (mean 0.3271), and opaque Hard Mix remains exact. A direct 1-pixel Core Image probe confirmed the opaque equality threshold is off; the new Linux regression observed RED before the quantized-premultiplied-surface correction and GREEN after. The new Ubuntu archive `candidate-hardmix-20261003.tar` (SHA-256 `ceedfe8c6c53db47a4b2747c5c6bd83054e278ecc2c9479e839fc7fce6a2514c`) passes all 301 native Linux tests with the Mac fixtures mounted, no skips. The five native Wayland GUI/save checks pass on this same archive. Nondefault Motion Blur still reaches 51 (mean 3.7803), and Mac-produced PSD/PSB imports still render differently from Mac PNG by up to 53 (mean 0.526); fidelity remains HOLD. A fresh Xcode Mac Photoshop reference test passed (`MacPhotoshopReferenceResults-20261003-2.xcresult`), and the direct Mac Core Image probes reproduce the Mac Hard Mix and Motion Blur PNGs byte-for-byte. The same archived source in a pinned x86_64 Arch container passes ordinary-user install, reinstall, renderer/project/PNG smoke and launcher removal. Its `makepkg` check passed 298 tests and skipped 3 Mac-fixture-dependent cases (the corpus is not bundled with the package); both app and debug packages were built. Its installation was tested as a user launcher, not as a system-installed pacman package. The independent source-fidelity review confirmed the Motion Blur and PSD/PSB-versus-Mac-PNG differences as release blockers. A separate security review found a high-severity merged-only PSD/PSB memory-exhaustion path: 56 declared channels could all be decoded despite only RGB and optional alpha being used. A test-first fix decodes at most four channels and bounds/skips the rest, while still rejecting truncated extra-channel payloads. Three new regressions failed before the fix and passed afterward; the full local PSD suite passes 18 tests. The new frozen source `candidate-psd-guard-20261003.tar` (SHA-256 `61f50c0372d93ea6558298368d302a0f882b2e82cd72a2025378146d8f1c86a6`) passes 305 Ubuntu ARM64 tests with 3 fixture-dependent skips. Its 68 Mac v11 projects load/save/reopen; 25 renders are byte-exact, while nondefault Motion Blur still differs by up to 51. Real Mac PSD/PSB projects import and render identically to Mac-saved project bundles on Linux, but their Mac PNG references still differ by up to 66. The same source passes five native Weston checks; in a fresh x86_64 Arch container it passes ordinary-user install/reinstall/removal, `makepkg` (305 passed, 3 fixture-dependent skips), and an actual `pacman -U` install, installed-kernel/editor smoke, desktop validation and `pacman -R` removal. An independent read-only re-review found that the prior extra-channel allocation path is closed and its bounded malformed/truncated-input probes pass; it did not find the archive in its isolated environment, so the parent separately verified the archive SHA-256 and byte-for-byte identity of the reviewed PSD code and tests. This closes that specific memory-exhaustion finding, not the broader release security gate. No publication has occurred.

SVG adversarial qualification (2026-10-03): a stale `stat()` could let a replaced SVG exceed the stated 4 MiB read bound; QtSvg also silently dropped deeply nested shapes, while accepting a 10,001-node flat scene. Each targeted test failed first. The importer now reads at most the limit plus one byte and refuses depth over 32 or more than 10,000 nodes before rendering. The targeted PSD/SVG suite passes 27 tests; the new Ubuntu ARM64 source snapshot `candidate-svg-guard-20261003.tar` (SHA-256 `48c8323f52a172cc4edfae606c7fe79de405f1f6c67e71c16ed0bbb76f444fee`) passes 308 tests with 3 fixture-dependent skips. This newer snapshot has not yet completed the Arch, native Wayland, and Mac interchange reruns; prior snapshot evidence is not silently inherited.

Prior opacity-only Linux snapshot (2026-10-03): the Mac Photoshop reference at (14,14) exposed separate rounding of a half-opacity folder and its Hard Mix child; one effective opacity matches the Mac green channel (145 rather than 211), leaving 1-value red/blue differences at that pixel. The regression failed before and passes after the change. The full PSD/PSB comparison still has four blue-channel threshold flips elsewhere (max 53, mean 0.4863) that are under a bounded Core Graphics/Core Image byte-surface investigation; this is not parity. A new immutable source archive `candidate-psd-opacity-20261003.tar` (SHA-256 `1d4e612f69db680c4f70d658fc806cc069c8825d8c4b621375f62833fbfbbedf`) includes this correction and the SVG guard. Native Ubuntu ARM64 builds the C extension and reports 309 passed, 3 fixture-dependent skips without the external corpus; with the actual Mac reference corpus mounted, all 312 tests pass with no skips. All 68 Mac projects load/render/save/reopen (25 exact), PSD/PSB imports match the Linux rendering of the saved Mac projects and round-trip identically, and the 5-check native Wayland run passes. A native x86_64 Arch ordinary-user install/reinstall/removal smoke and `makepkg` check both pass against this archive: 309 passed, 3 Mac-fixture-dependent skips, with app and debug packages built. A separate `pacman -U` installed the new x86_64 artifact into `/usr/lib/python3.14/site-packages` and the installed renderer produced the expected Hard Mix group pixel `(250,145,28,186)` outside the checkout. The initial Arch smoke failed only because the reused container lacked the new archive mount; `podman cp` transferred and SHA-256-verified the archive before rerun. The first package command selected the install-smoke virtualenv without `build`; explicitly using Arch's `/usr/bin/python` passed. The current worktree's macOS Xcode core suite passed 512/512 tests and its native-window suite passed 7/7 (separate result bundles `MacPostSvgCoreResults.xcresult` and `MacPostSvgWindowResults.xcresult`). The current snapshot has not received final independent security/source-fidelity signoff or remote CI; Mac/Linux numerical parity remains blocked. Do not publish while Motion Blur and PSD/PSB numerical fidelity remain unresolved.

Required gates: format 1–11/schema and adversarial input safety; shared C ABI and portable feature tests; renderer/editor regressions; Linux full test/lint/format/build; independent wheel install and .comp/PNG roundtrip; native Wayland path; Mac/Linux interchange where changed; fresh Arch x86_64 install and CI; independent source-fidelity and security/correctness review. Missing evidence is HOLD, never PASS. Platform exclusions: Apple-only system integration, Metal runtime and Vision replaced by documented Linux equivalents.

Hard Mix surface correction (2026-10-03): the four residual Photoshop-reference mismatches reproduce exactly in isolated Mac Core Graphics/Core Image contexts. A parent-run sweep of 1,001 opacity values over 7 input pixels confirms that Core Graphics first rounds original premultiplied RGBA8, separately rounds global alpha to a 255-level factor, and then multiplies those integer surfaces (zero mismatches against this model; a floor-256 model fails 249 opacity cases). The Linux correction preserves both stages, evaluates the strict Hard Mix threshold by integer cross-multiplication, and returns through the final premultiplied RGBA8 quantization boundary. Four new regressions failed first and now pass; the full renderer qualification suite passes 12 tests. Both real Mac-produced PSD and PSB fixtures now render byte-exactly like Mac PNG on the host, preserve structure, and round-trip identically. Native snapshot qualification was pending at this older checkpoint and later passed; no broad Photoshop fidelity claim is implied by these two fixtures.

Superseded 165-file qualification snapshot (2026-10-03): `candidate-hardmix-surfaces-20261003.tar`, SHA-256 `381a3a2cad6427327a65d626efc540103a9572ba47c1d6f84f42e0b6e7b8995c`. The parent-built native Ubuntu ARM64 extension and full test suite pass all 316 tests with the Mac corpus mounted. All 68 Mac projects load/render/save/reopen, with 26 pixel-exact PNG comparisons; both Hard Mix fixtures and both real PSD/PSB imports are byte-exact against the Mac references. Native Wayland passes all 5 checks. Native x86_64 Arch's package check passes 313 tests, skipping only 3 external-corpus cases, and builds both app/debug packages. Its user installer/reinstaller/remover passes; the system-installed pacman artifact reproduces all four Core Image sample pixels exactly outside the checkout. All 165 archived source files match the Arch test directory byte-for-byte. Ruff lint/format and index/worktree whitespace gates pass. Independent review verified the archive SHA and found no in-scope Hard Mix, PSD, or SVG regression, but this is not global release signoff. The reviewer's live SVG scene-limit probe was approval-blocked; after explicit user approval, the parent reran the three existing adversarial SVG limit tests locally and all passed. At this earlier checkpoint, nondefault Motion Blur (max 51, mean 3.7803) was considered a visual-fidelity blocker; the maintainer later accepted it as documented above. Smaller corpus residuals were likewise later accepted as documented Linux differences, not a blanket parity claim: Hue blend has straight/premultiplied maxima 13/9, Hue/Saturation 8/7, Gaussian Blur 7/1, Vivid Light 7/7, Color Dodge 6/6, and group-guides 6/1; their alpha deltas are at most 1. No commit, push, PR, or default-branch merge has occurred.

Superseded next step: the maintainer later accepted nondefault Motion Blur and the smaller numerical deltas as documented Linux differences. That explicit decision updates the former pixel-parity gate; safety, local qualification, and remote CI gates still apply. Apple delegates to private Core Image behavior: a bounded unmanaged-float impulse probe did not validate convolution against the fixture; a follow-up parent probe measured different impulse masses for otherwise identical filters on 16-, 17-, 33-, and 129-pixel square extents (1.008262, 0.991406, 0.979002, 0.965159), so a single extent-independent measured kernel is not established. The empirically fitted 1.05-scale Gaussian (max 47 on one fixture) is not a general parity fix. At this checkpoint, the planned next work was to classify residuals, freeze and qualify the code, review the merge/index, and obtain remote CI. The final snapshot and acceptance are recorded above; only the agreed remaining GitHub gates apply. Runtime receipts live under ignored `.runtime/upstream-port/`. Existing release evidence below is historical, not acceptance evidence for this branch.

---

Linux build: **0.1.1**, early release. The base port is merged in
[PR #1](https://github.com/dlpwaters/compositor/pull/1). This milestone adds a
fresh-machine source installer, a detailed user guide, a native workspace 5
screen recording, refreshed screenshots, and an editable synthetic poster.
The [0.1.1 preview](https://github.com/dlpwaters/compositor/releases/tag/linux-v0.1.1)
provides the installation target for this guide. The installer and media update
is tracked in [PR #2](https://github.com/dlpwaters/compositor/pull/2).
Baseline: Compositor 1.0.4 (`a19db9011282399785dc18efcfded904627bdcc2`).
The original Swift sources and eight C kernels remain unchanged.

The native Qt/Wayland editor implements layered compositing, hierarchy, masks
and clipping, transforms and distortion, selections, painting and retouching,
six editable adjustments, filters, local background removal, tabs, import/export,
and clipboard. The tool rail and common actions use **28 original monochrome
SVGs**. Linux adds editable text and a New Layer transparent/solid fill chooser.
The app and desktop launcher are named **Compositor Linux**; `compositor-linux`
is the primary command and `compositor` remains an alias.

Verified on Arch/Omarchy x86_64 with Python 3.14:

- **137 tests** pass. Coverage includes strict project validation, atomic saves,
  rendering, masks/clipping, geometry, adjustments, painting, undo/redo, pending
  edits, text create/edit/round-trip/rasterization, transformed text anchors,
  cancellation, closing during a preview, solid fills and double-click recoloring,
  read-only installer preflight, contradictory options, path quoting, reinstall,
  removal, unrelated launcher preservation and symlink preservation.
  Ruff, formatting, shell syntax and Desktop Entry validation pass.
- Native Wayland checks exercise the actual text and New Layer dialogs, canvas
  Text tool events, preview/commit, text editing/undo, cancellation, export, and
  save/reopen with identical rendered pixels. Published screenshots show only
  the synthetic sample project in the application's Fusion theme. The 0.1.1
  finite native demo passes all five checks, including actual text typing,
  editing/undo, solid-layer recoloring/undo, project reopening and PNG export.
  The 37-second H.264 video and animated preview were recorded on workspace 5
  and visually reviewed; no personal content or audio is included.
- The 0.1.0 installed release launcher passed all 14 broad native Wayland smoke checks:
  brush strokes, gradient/transform/crop Apply/Cancel,
  Hue/Saturation targeting, original-pixel Levels sampling, distortion, and
  clipboard Copy Merged/Paste with an independent `wl-paste` reader. The later
  broad smoke rerun after the icon update stopped at its window-focus prerequisite;
  the final release run passes that prerequisite and all checks. These tests use
  Qt event delivery and do not establish physical keyboard delivery.
- Wheel and source builds succeed. An independently installed wheel outside the
  checkout renders all 28 SVGs and edits/renders saved text layers. The original
  MIT notice is present in the wheel and distributions.
- The 0.1.1 Arch package creation with development dependencies passes 137 tests and
  includes both commands, all 28 SVGs, the text renderer, license, notices and
  screenshots, footage and the editable sample. Its entrypoint uses `/usr/bin/python`;
  Qt SVG and Wayland are explicit dependencies. Source archives exclude prior Arch build directories.
  Build dependency resolution was skipped with
  `makepkg --nodeps`; system installation was not performed.
- The 0.1.1 per-user installer passes a fresh **official Arch x86_64 container**
  check with system dependencies resolved by pacman, a new ordinary user and no
  existing Python environment/cache. Installation, reinstall, the actual command,
  SVG/text rendering, native kernels, save/reopen/export, desktop validation and
  launcher removal all pass. The model is not downloaded implicitly. This is
  headless qualification, separate from the native Wayland demo on this host.
  The optional graphical polkit/pacman authorization was not exercised in the
  container. A clean system **package** installation and aarch64 remain pending.
- The updated installer and desktop entry work on the Omarchy host and preserve
  unrelated launcher files. Actual local U2NETP inference and its pinned checksum were
  verified earlier; model weights are excluded from the repository/packages.
- `pip-audit` found no known advisories in 21 installed dependencies (the editable
  application itself is excluded from the registry lookup). This covers known
  dependency advisories, not an independent application security audit.

[Linux CI](https://github.com/dlpwaters/compositor/actions/workflows/linux.yml)
runs Python 3.12/3.14 tests, lint/format, Desktop Entry validation and isolated
wheel/source builds. A new pinned Arch container job exercises the per-user
installer and explicitly verifies root-install refusal. Consult the workflow
for the result at a specific commit. The
[0.1.1 milestone run](https://github.com/dlpwaters/compositor/actions/runs/36756904123)
passes all three jobs at `f92391c`. The earlier
[0.1.0 merged-main run](https://github.com/dlpwaters/compositor/actions/runs/36745781801)
passes both Python jobs at `a95b24d`; a fresh public clone at that commit also
built the kernels and passed its 129 tests.

This is **not a certified full 1:1 replacement**. The
[parity audit](linux-parity.md) separates implemented behavior from qualification.
Mac-generated reference projects and cross-platform interaction tests remain
necessary. CPU/Core Image sampling and blur, Qt appearance, Apple Vision
substitution, selected-pixel transform controls, large-photo performance,
additional inter-app drops, aarch64 and clean system package installation need further
qualification. Linux text uses a saved PNG fallback; Mac resaving can discard
editability.

Next engineering step: collect the Mac reference corpus, compare with
`scripts/linux-compare.py`, and qualify the remaining interactions and package
installation on a clean Arch system.

## Upstream maintenance

[PR #5](https://github.com/dlpwaters/compositor/pull/5) is merged into `main`.
Both workflows are active: the stable-release report runs daily at 14:23 UTC,
and Linux CI runs Mondays at 13:41 UTC to catch dependency drift. A single
tracking issue updates only when report content changes. The baseline remains
pinned to 1.0.4; this setup does not implement upstream features or newer formats.

The initial live report resolves v1.4.5 to
`086f1631573ccb2b57644e53b52bf1488fc976aa`, with 316 commits and 207 changed
paths since baseline, including 10 shared C paths. Its project-format version
is 11; Linux accepts 1–7. Review compatibility before sharing newer Mac saves.
See [maintenance](upstream-maintenance.md) for the integration checklist and
[the Hermes prompt](hermes-daily-prompt.md) for a separate daily digest.

Local validation passes 156 tests, including 19 monitoring regressions covering
complete diffs beyond 300 files, schema version detection, divergent history,
stable report content, API failure, pinned issue updates and duplicate prevention.
Ruff lint/format and actionlint validation of both workflows pass. The live
watcher produced the initial report without changing the source checkout.
The initial report is published in [issue #3](https://github.com/dlpwaters/compositor/issues/3).
Its number is pinned to avoid discovery races in immediately repeated checks.

[Merged-main Linux CI](https://github.com/dlpwaters/compositor/actions/runs/37130635255)
passes Python 3.12/3.14 and the fresh Arch installation job at `c7ee747`.
The [initial watcher run](https://github.com/dlpwaters/compositor/actions/runs/37130633415)
passes both report generation and the default-branch issue publisher; it reports
the existing issue unchanged. The Hermes daily task can be created separately
with the linked prompt.

Next maintenance step: prioritize project formats 8–11 and changed C kernels
in scoped update PRs. Native Wayland and real Mac/Linux round-trip qualification
remain required for those changes.
