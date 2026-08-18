"""Registry mapping provider names to `AtsProvider` singleton instances."""

from __future__ import annotations

from app.services.ats.ashby import AshbyProvider
from app.services.ats.base import AtsProvider
from app.services.ats.greenhouse import GreenhouseProvider
from app.services.ats.lever import LeverProvider
from app.services.ats.workable import WorkableProvider

PROVIDERS: dict[str, AtsProvider] = {
    "greenhouse": GreenhouseProvider(),
    "lever": LeverProvider(),
    "ashby": AshbyProvider(),
    "workable": WorkableProvider(),
}


def get_provider(name: str) -> AtsProvider:
    try:
        return PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown ATS provider {name!r}. Known providers: {sorted(PROVIDERS)}"
        ) from exc
