"""Every gigs test mounts or runs gigs; none may write the operator's real `~/.gigai` (a gig's folder lands under
`$GIGAI_HOME`, else `~/.gigai`). Subprocess helpers set their own HOME/GIGAI_HOME on top of this."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _scratch_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    monkeypatch.delenv("GIGAI_HOME", raising=False)
