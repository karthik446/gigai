"""Verify the packaged Scout UI (U13) is present in an installed distribution.

Unlike tools/verify_installed_schemas.py, this does not pin exact file
digests: Vite's output hashes change on every UI source edit by design (the
PR "Scout UI bundle is up to date" job in .github/workflows/pull_request.yaml
is what keeps the committed dist/ fresh against source). This verifier only
checks that the build landed in the installed package at all, is non-empty,
and that index.html references a real, present hashed asset -- catching a
package-data glob that silently stops shipping the UI.
"""

from __future__ import annotations

from importlib import resources
import re


def main() -> int:
    root = resources.files("gigai.scout").joinpath("ui", "dist")
    if not root.is_dir():
        raise SystemExit(
            "installed gigai.scout package is missing ui/dist: the Scout UI "
            "was not shipped in this wheel/sdist (check "
            "[tool.setuptools.package-data] 'gigai.scout' in pyproject.toml)"
        )

    index_path = root.joinpath("index.html")
    if not index_path.is_file():
        raise SystemExit("installed gigai.scout ui/dist is missing index.html")
    index_html = index_path.read_text(encoding="utf-8")

    assets_dir = root.joinpath("assets")
    if not assets_dir.is_dir():
        raise SystemExit("installed gigai.scout ui/dist is missing the assets/ directory")
    asset_names = {item.name for item in assets_dir.iterdir() if item.is_file()}
    if not asset_names:
        raise SystemExit("installed gigai.scout ui/dist/assets is empty")

    referenced = set(re.findall(r'(?:src|href)="/assets/([^"]+)"', index_html))
    if not referenced:
        raise SystemExit("installed gigai.scout ui/dist/index.html references no /assets/ files")
    missing = sorted(referenced - asset_names)
    if missing:
        raise SystemExit(
            f"installed gigai.scout ui/dist/index.html references assets not shipped in the "
            f"wheel/sdist: {missing}"
        )

    for name in referenced:
        content = assets_dir.joinpath(name).read_bytes()
        if not content:
            raise SystemExit(f"installed gigai.scout ui/dist/assets/{name} is empty")

    print(
        f"verified installed Scout UI: index.html + {len(asset_names)} asset(s) "
        f"({', '.join(sorted(referenced))})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
