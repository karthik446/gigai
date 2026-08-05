"""Verify the operating-system boundaries of the Debian offline CI lane."""

from __future__ import annotations

import os
from pathlib import Path
import sys


_AUDIT_ROOTS = (
    "GIGAI_AUDIT_HOME",
    "GIGAI_AUDIT_TARGET",
    "GIGAI_AUDIT_WORKPAD",
)
_SENSITIVE_ENVIRONMENT_NAMES = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    }
)


def _mount_points() -> set[str]:
    mounts: set[str] = set()
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= 5:
            mounts.add(fields[4])
    return mounts


def main() -> None:
    if sys.platform != "linux":
        raise SystemExit("Debian offline verification requires Linux")
    if os.geteuid() == 0:
        raise SystemExit("Debian offline verification must not run as root")
    unexpected = sorted(
        name
        for name in os.environ
        if name in _SENSITIVE_ENVIRONMENT_NAMES
        or (
            name.startswith("GIGAI_")
            and any(part in name for part in ("KEY", "SECRET", "TOKEN"))
        )
    )
    if unexpected:
        raise SystemExit(
            "Debian offline verification received sensitive environment names: "
            + ", ".join(unexpected)
        )
    roots = tuple(Path(os.environ[name]) for name in _AUDIT_ROOTS)
    if len({root.resolve(strict=True) for root in roots}) != len(roots):
        raise SystemExit("Debian offline audit roots must be distinct")
    mounts = _mount_points()
    for root in roots:
        resolved = root.resolve(strict=True)
        if not resolved.is_dir() or resolved.is_symlink() or str(resolved) not in mounts:
            raise SystemExit(f"Debian offline audit root is not a direct mount: {root}")
        if not os.access(resolved, os.W_OK):
            raise SystemExit(f"Debian offline audit root is not writable: {root}")
    print("verified Debian offline non-root, network-isolated audit mounts")


if __name__ == "__main__":
    main()
