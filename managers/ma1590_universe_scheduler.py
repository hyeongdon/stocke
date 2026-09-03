"""Backward-compat shim — use managers.ma1592_universe_scheduler."""
from managers.ma1592_universe_scheduler import (  # noqa: F401
    Ma1592UniverseScheduler as Ma1590UniverseScheduler,
    ma1592_universe_scheduler as ma1590_universe_scheduler,
)
