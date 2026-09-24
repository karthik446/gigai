"""R0: reserved for F1-c's profile-management routes.

Empty on purpose -- this packet (R0-present-api-split) is a pure move of
the existing config/setup/discover/runs/static routes; F1-c adds the
profiles routes (and their own ``ProfilesRoutesMixin``) into this module in
a later packet.
"""

from __future__ import annotations
