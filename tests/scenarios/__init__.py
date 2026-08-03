"""Reusable installed-process scenario harness for GigAI goals."""

from .harness import (
    CommandTarget,
    InstalledGigAI,
    ScenarioHarness,
    ScenarioResult,
    ScenarioRoots,
    ScenarioSpec,
    ScenarioViolation,
    TreeManifest,
    copy_fixture_repository,
    create_recording_substitute,
    invoke_recording_substitute,
)

__all__ = [
    "CommandTarget",
    "InstalledGigAI",
    "ScenarioHarness",
    "ScenarioResult",
    "ScenarioRoots",
    "ScenarioSpec",
    "ScenarioViolation",
    "TreeManifest",
    "copy_fixture_repository",
    "create_recording_substitute",
    "invoke_recording_substitute",
]
