"""R0: route modules for the Scout find-jobs API.

Split out of ``present_api.py`` (a pure move; see that module's docstring).
``present_api.py`` remains the import-compat shim and the
``python -m gigai.scout.find_jobs.present_api`` entry point -- import from
here directly only if you're one of this package's own siblings.
"""

from __future__ import annotations
