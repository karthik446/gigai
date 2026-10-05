"""RELEASE GATE (0.1.11 MODELPIN2): the accuracy counts must be in the model table before this ships.

RED ON PURPOSE while ``src/gigai/scout/evaluated_models.py`` still holds ``RESULTS_PLACEHOLDER = True``. To make it
green: fill ``accurate`` for every row of ``RESULTS`` from the fourth set's key-scored result files, then set
``RESULTS_PLACEHOLDER = False``. Until the numbers land, skip it by name:
``pytest --deselect tests/behaviors/ci_tooling/test_model_results_filled.py``.
"""

from __future__ import annotations

from gigai.scout import evaluated_models as table


def test_the_fourth_set_counts_are_in_the_model_table() -> None:
    assert not table.RESULTS_PLACEHOLDER, (
        "edit src/gigai/scout/evaluated_models.py: fill the `accurate` counts of RESULTS from the fourth set's result "
        "files, then set RESULTS_PLACEHOLDER = False"
    )
    missing = [f"{row.target}/{row.model}" for row in table.RESULTS if row.accurate is None]
    assert not missing, f"no accurate count yet for {missing}: edit src/gigai/scout/evaluated_models.py"
