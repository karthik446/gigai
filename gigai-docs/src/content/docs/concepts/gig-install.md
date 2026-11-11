---
title: Install a gig
description: Add, update and remove gigs in the environment gigai runs from, without changing the gigai version.
---

A gig is a Python package that adds its own `gigai <gig>` command (see
[Gigs and goal graphs](../gigs/)). Three commands manage the gigs installed
next to gigai. They use [uv](https://docs.astral.sh/uv/), so `uv` must be on
your `PATH`.

```
gigai gig install <spec>     # a wheel, an sdist, a project folder, or a package name
gigai gig update <gig>       # reinstall one gig, at its newest release that works with your gigai
gigai gig uninstall <gig>    # remove one gig
```

`<spec>` is a path to a gig's wheel (`.whl`), sdist (`.tar.gz`) or project
folder, or a package name from your package index. `<gig>` is the gig's
command name (`hello`) or its package name. Add `--dry-run` to any of the
three to print the exact `uv` command without running it.

## Where gigs go

Gigs are installed into the same environment as gigai, and gigai's own
version does not change.

- **Installed with `uv tool install gigai`** (the [recommended
  install](../../install/)): uv keeps a record of everything in that tool
  environment. Each command rebuilds the environment from that record, so it
  keeps every other gig you installed, the Python version and the index or
  `--find-links` settings you installed gigai with. `gigai gig install` runs
  two commands like these, the first with the gigai you have pinned, the second
  without the pin:

  ```
  uv tool install gigai==0.2.0 --with <every other gig> --with <spec> --python 3.12
  uv tool install gigai --with <every other gig> --with <spec> --python 3.12
  ```

  The second command keeps everything the first one installed and leaves no
  pin in uv's record, so `uv tool upgrade gigai` upgrades gigai later and keeps
  your gigs. The commands always rebuild the tool environment gigai runs from
  and keep the `gigai` command where it is, even if `UV_TOOL_DIR` or
  `UV_TOOL_BIN_DIR` in your shell point somewhere else.
- **Installed in a virtual environment**: the gig is installed with
  `uv pip install` into that venv. The gigai version you have is pinned for the
  install, so a gig can't pull in a different gigai.

pipx is not supported, and neither is a system Python outside any venv. In
those cases the commands install nothing, print the supported ways
(`uv tool install gigai`, or a venv) and exit with status 2.

If `uv` is not on your `PATH`, the command prints the `uv` command it would
run and exits with status 2, so you can install uv and run it yourself.

## What gets checked

- **What you're installing.** `gigai gig install gigai` is refused: gigai
  isn't a gig. Upgrade it with `uv tool upgrade gigai` (or in its venv).
- **The gig's command name.** A gig can't use a name that is already taken:
  a gigai command such as `doctor`, `gig` or `scout`, or the command of a gig
  from another package you already installed. A wheel, sdist or project folder
  that declares such a name is refused before anything is installed. A package
  name from an index is checked once it is installed, and then undone: in a uv
  tool environment the previous set of gigs is put back; in a venv the package
  is removed again, unless it was already installed before the command, in
  which case it is left as it is. Either way, the command exits with status 2.
- **The gigai version the gig needs.** gigai's version never changes because
  of a gig. If the gig needs a newer gigai than the one you have, uv can't
  install it with your gigai pinned, so nothing changes: the command prints a
  message that names both versions, for example
  ``gig gigai-eval-book needs gigai >=0.3, you have 0.2.0; run `uv tool upgrade gigai` first``,
  and exits with status 2. In a uv tool environment, should gigai still move
  in the second, unpinned command, gigai puts gigai and your gigs back as they
  were and exits with status 2; if even that fails, it names the command that
  puts gigai back (`uv tool install gigai==<version you had>`), after which you
  install your gigs again.

  `gigai gig update` moves only that gig, to its newest release that works
  with the gigai you have; other gigs and gigai stay where they are. That can
  be an older release than the one you have: if the installed release is gone
  from the index, the update moves the gig back to the newest release that is
  still there and works with your gigai. If no release left works with your
  gigai, the update fails as above and nothing changes.
- **The gig contract.** After an install or update, `gigai gig check <gig>` runs
  on the installed gig and prints its result. The gig stays installed even if
  some checks fail.

## Uninstall and the gig folder

`gigai gig uninstall <gig>` removes the gig's package. The gig folder
`<home>/gigs/<gig>/` (its settings and data) is kept, so installing the gig
again picks up where it left off. You can't uninstall gigai itself, or a gig
that ships with it such as Scout, this way.

## Roll back gigai

To go back to an older gigai, reinstall it at that version:

```
uv tool install gigai==<old>
```

That rebuilds the tool environment with gigai only. If `<old>` is 0.2.0 or
later, install your gigs again with `gigai gig install`. Older versions (0.1.x)
have no `gigai gig` command, so your gigs stay gone until you upgrade again;
then install them again. The `==<old>` pin stays: `uv tool upgrade gigai` and
`gigai gig install` keep gigai at that version. Running
`uv tool install gigai` without the pin removes it, and also rebuilds with
gigai only.

See the [CLI reference](../../reference/cli/) for every option.
