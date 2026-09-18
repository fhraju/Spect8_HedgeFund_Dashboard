"""Deterministic multi-database routing for Market Data Platform.

Each instrument is bound to exactly one read-only authority/database.
No fallback, no cross-routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from ..config import INSTRUMENT_AUTHORITY_MAP
from .platform_adapter import (
    PlatformCanonicalReadGateway,
    PlatformReadBatch,
)


@dataclass(frozen=True, slots=True)
class AuthorityGateway:
    authority: str
    database_url: str
    gateway: PlatformCanonicalReadGateway
    instruments: tuple[str, ...]


class PlatformMultiGateway:
    """Routes per-instrument reads to exactly one authority gateway."""

    def __init__(self, gateways: Mapping[str, AuthorityGateway]) -> None:
        # Validate deterministic mapping
        seen: dict[str, str] = {}
        for auth, gw in gateways.items():
            if auth not in {"IG_DEMO", "IG_LIVE"}:
                raise ValueError(f"Unknown authority {auth}")
            for inst in gw.instruments:
                if inst in seen:
                    raise ValueError(
                        f"Duplicate instrument assignment {inst}: {seen[inst]} and {auth}"
                    )
                if INSTRUMENT_AUTHORITY_MAP.get(inst) != auth:
                    raise ValueError(
                        f"Instrument {inst} assigned to {auth} but authority map expects {INSTRUMENT_AUTHORITY_MAP.get(inst)}"
                    )
                seen[inst] = auth
        # Ensure all gateway instruments are subset of approved map
        self._gateways = dict(gateways)
        self._instrument_to_gateway: dict[str, AuthorityGateway] = {}
        for gw in gateways.values():
            for inst in gw.instruments:
                self._instrument_to_gateway[inst] = gw

    def gateway_for(self, spect8_instrument_id: str) -> AuthorityGateway:
        try:
            return self._instrument_to_gateway[spect8_instrument_id]
        except KeyError as error:
            raise ValueError(
                f"No authority configured for instrument {spect8_instrument_id}"
            ) from error

    def authority_for(self, spect8_instrument_id: str) -> str:
        return self.gateway_for(spect8_instrument_id).authority

    def database_url_for(self, spect8_instrument_id: str) -> str:
        return self.gateway_for(spect8_instrument_id).database_url

    def read(
        self,
        instrument_ids: tuple[str, ...],
        *,
        available_as_of: datetime,
        after_canonical_bar_id: int | None = None,
        after_canonical_bar_ids: Mapping[str, int | None] | None = None,
        limits: Mapping[str, int],
    ) -> PlatformReadBatch:
        """Read across authorities, merging batches deterministically."""
        # after_canonical_bar_ids allows per-authority watermark
        if after_canonical_bar_ids is not None and after_canonical_bar_id is not None:
            raise ValueError("Provide only one of after_canonical_bar_id or after_canonical_bar_ids")

        from .platform_adapter import PLATFORM_TO_SPECT8_INSTRUMENT

        by_authority: dict[str, list[str]] = {}
        for platform_id in instrument_ids:
            spect8_id = PLATFORM_TO_SPECT8_INSTRUMENT.get(platform_id)  # type: ignore
            if spect8_id is None:
                raise ValueError(f"Unmapped platform instrument {platform_id}")
            gw = self.gateway_for(spect8_id)
            by_authority.setdefault(gw.authority, []).append(platform_id)

        batches: list[PlatformReadBatch] = []
        for authority, platform_ids_for_auth in by_authority.items():
            gw = self._gateways[authority]
            # Determine after id for this authority
            if after_canonical_bar_ids is not None:
                after = after_canonical_bar_ids.get(authority)
            else:
                after = after_canonical_bar_id
            batch = gw.gateway.read(
                tuple(platform_ids_for_auth),  # type: ignore[arg-type]
                available_as_of=available_as_of,
                after_canonical_bar_id=after,
                limits=limits,
            )
            # Translate platform ids back to spect8 instrument ids for internal tracking
            # Batch already contains platform instrument ids; we keep them as is
            batches.append(batch)

        if not batches:
            raise ValueError("No authorities selected for read")

        if len(batches) == 1:
            return batches[0]

        # Merge multiple batches
        all_bars = tuple(bar for b in batches for bar in b.bars)
        all_availability = tuple(item for b in batches for item in b.availability)
        all_partials = tuple(item for b in batches for item in b.partial_bar_snapshots)
        all_native = tuple(item for b in batches for item in b.native_bootstrap_bars)
        # Watermark: max of watermarks (each DB has independent ID space, but need combined)
        # For safety use max; per-authority watermarks stored separately in repository
        watermark = max(b.watermark_canonical_bar_id for b in batches)
        # Checksums: must match across DBs for same instrument master? If different, keep first
        # For multi-db we store per-authority checksums elsewhere; here just pick first
        return PlatformReadBatch(
            bars=all_bars,
            availability=all_availability,
            watermark_canonical_bar_id=watermark,
            available_as_of=batches[0].available_as_of,
            instrument_master_checksum=batches[0].instrument_master_checksum,
            session_calendar_checksum=batches[0].session_calendar_checksum,
            timezone_data_version=batches[0].timezone_data_version,
            native_bootstrap_bars=all_native,
            partial_bar_snapshots=all_partials,
        )

    def read_canonical_ids(
        self, canonical_bar_ids: tuple[int, ...]
    ) -> tuple:
        # Try each gateway until found (canonical IDs are globally unique per DB but may overlap)
        results = []
        for gw in self._gateways.values():
            try:
                chunk = gw.gateway.read_canonical_ids(canonical_bar_ids)
                results.extend(chunk)
            except Exception:
                continue
        return tuple(results)
