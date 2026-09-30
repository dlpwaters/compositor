# Security and data handling

Compositor Linux processes images locally. The editor does not upload documents,
run project-provided scripts, or download models automatically. The separate
model installer uses HTTPS and verifies the expected byte count and SHA-256.
Only load replacement ONNX models from sources you trust.

The `.comp` reader rejects paths outside the project, symlink asset files,
nonregular assets, duplicate JSON keys, nonfinite values, invalid UUID asset
names, broken or cyclic hierarchy/mask references, unsupported versions, and
oversized data. Limits include a 4 MiB manifest, 10,000 layers, 30,000-pixel sides,
and separate 100-megapixel source/mask budgets. Text settings are typed and bounded;
Qt receives plain text, so markup is not treated as image/resource instructions.
These limits reduce exposure and do not make image decoders a sandbox.

Saving writes a sibling staging directory, fsyncs content, and atomically replaces
an existing valid project. Failed validation or replacement keeps the old project.
The per-user installer refuses to replace unrelated primary launcher files and
preserves a conflicting compatibility alias. It refuses to run as root and does
not change Hyprland settings or add services. The explicit
`--install-system-deps` option requests graphical administrator authorization for
missing, named Arch prerequisites using `/usr/bin/pacman -S --needed`. Without
that option it performs no elevated actions. Dependency installation does not
refresh package databases or upgrade the system. Python dependencies remain in
a project-local virtual environment; optional model downloads remain explicit.

CI has read-only repository permissions, pinned actions, and no application
credentials. Tests cover damaged manifests, unsafe assets, allocation budgets,
atomic-save failures, and text preview cancellation. Dependencies are version
ranged; maintainers should repeat vulnerability checks and update packages before
publishing builds. A passing audit only covers known advisories for those versions.

Report reproducible bugs to this fork's Issues. For a vulnerability, use GitHub's
private vulnerability reporting if enabled; avoid posting a working exploit or
private project content publicly. Include the build version, dependency versions,
and a minimal synthetic reproducer. This project has not had an independent
security audit and provides no response-time guarantee.
