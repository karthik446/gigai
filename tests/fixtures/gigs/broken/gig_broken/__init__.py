"""Test-only gig whose import fails, as a gig with a missing dependency would."""

raise ImportError("gig_broken needs a dependency that is not installed")
