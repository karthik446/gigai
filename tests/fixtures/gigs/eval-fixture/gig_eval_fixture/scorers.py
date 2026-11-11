"""Code scorers the eval fixture's ``[evals]`` table names (``gig check`` item 11 imports them).

``exact_match`` takes the step's parsed output and the dataset row; the scorer contract that calls it (what it
returns, how it is recorded) is V4's ``eval run``.
"""


def exact_match(output, item):
    """1.0 when the parsed answer equals the row's ``expected`` (case and surrounding space ignored), else 0.0."""

    answer = output.get("answer") if isinstance(output, dict) else None
    if not isinstance(answer, str):
        return 0.0
    return 1.0 if answer.strip().casefold() == str(item.get("expected", "")).strip().casefold() else 0.0
