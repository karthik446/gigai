# Worker Report: readme-uv-only

## Summary

Removed all user-facing `pip install` instructions from GigAI documentation and replaced them with `uv tool install` equivalents, per the operator rule: "never pip, uv only."

## Changes Made

### README.md (2 changes, EXECUTED)

**Line 36 — Before:**
```bash
pip install gigai        # or: uv tool install gigai
gigai --version
gigai --help
```

**After:**
```bash
uv tool install gigai
gigai --version
gigai --help
```

**Line 203 — Before:**
```
Works from an installed package (`pip install gigai` / `uv tool install gigai`) — no source checkout needed:
```

**After:**
```
Works from an installed package (`uv tool install gigai`) — no source checkout needed:
```

## Files Checked (READ)

- `README.md` — 2 pip references found and fixed
- `docs/development/v0.1.8/runbooks/M1-find-jobs.md` — No pip references found
- `CHANGELOG.md` — No pip references found (Unreleased section checked)
- `src/gigai/scout/ui/README.md` — Does not exist

## Verification

```bash
grep -rniE '\bpip3? install' README.md docs/development/v0.1.8/runbooks CHANGELOG.md
# Returns: nothing (exit code 1)
```

All user-facing pip install instructions have been removed. The `.egg-info/` directory contains auto-generated copies that will be regenerated during the next build.

## Follow-up: readme-uv-only-r1 (EXECUTED)

Fixed broken instruction under "Start the API" section that assumed `python` could import the installed package.

**Line 206 — Before:**
```bash
python -m gigai.scout.find_jobs.present_api --home ~/.gigai --target /path/to/target/repo
```

**After:**
```bash
uv tool run --from gigai python -m gigai.scout.find_jobs.present_api --home ~/.gigai --target /path/to/target/repo
```

### Verification (EXECUTED)

```bash
$ uv tool run --from gigai==0.1.8.1 python -c "import gigai.scout.find_jobs.present_api; print('ok')"
ok
```

The `uv tool run --from gigai` form correctly runs Python with access to the gigai package from uv's tool environment, allowing the module to be imported and executed.
