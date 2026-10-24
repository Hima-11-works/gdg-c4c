"""PostgreSQL-backed provider budgets shared by workers and CLI commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from app.models.tables import provider_budget_state, provider_request_usage


@dataclass(frozen=True, slots=True)
class BudgetWindow:
    seconds: int
    units: int


@dataclass(frozen=True, slots=True)
class ProviderPolicy:
    windows: tuple[BudgetWindow, ...]
    max_bytes_per_day: int = 0
    max_in_flight: int = 0
    in_flight_lease_seconds: int = 20 * 60


POLICIES: dict[str, ProviderPolicy] = {
    # Local ceilings preserve at least half of OpenAQ's published hourly
    # allowance for discovery, retries, and other installations on the key.
    "openaq": ProviderPolicy((BudgetWindow(60, 30), BudgetWindow(3600, 1000))),
    # Upper-bound location-equivalent accounting; the daily target is below
    # the free service's stated 10,000-call limit.
    "open-meteo": ProviderPolicy(
        (BudgetWindow(60, 300), BudgetWindow(3600, 2500), BudgetWindow(86400, 5000))
    ),
    # Charge five local transaction units per bounded AOI request.
    "firms": ProviderPolicy((BudgetWindow(600, 60),)),
    # CDSE has no universal OData quota. Use an application ceiling, serialize
    # product access, and reserve a conservative 2 GB/day transfer allowance.
    "cdse": ProviderPolicy(
        (BudgetWindow(60, 10), BudgetWindow(3600, 240)),
        max_bytes_per_day=2_000_000_000,
        max_in_flight=1,
        in_flight_lease_seconds=20 * 60,
    ),
    # GIBS limits are application safeguards, not claims about NASA quotas.
    "gibs": ProviderPolicy((BudgetWindow(60, 60), BudgetWindow(86400, 5000))),
    "satellite-tile": ProviderPolicy((BudgetWindow(60, 60), BudgetWindow(86400, 5000))),
    # CAMS regional background operates within bounded free Copernicus / Open-Meteo
    # API allowances; kept strictly within hourly and daily limits.
    "cams": ProviderPolicy(
        (BudgetWindow(60, 10), BudgetWindow(3600, 100), BudgetWindow(86400, 1000)),
        max_bytes_per_day=50_000_000,
    ),
    # Gemini photo advisory analysis uses a measured rate limit to protect
    # against quota exhaustion and prevent advisory spikes.
    "gemini": ProviderPolicy(
        (BudgetWindow(60, 15), BudgetWindow(3600, 300), BudgetWindow(86400, 1500)),
    ),
}


@dataclass(frozen=True, slots=True)
class Reservation:
    allowed: bool
    reservation_id: int | None = None
    retry_at: datetime | None = None
    reason: str | None = None


class SqlProviderRequestBudget:
    """Reserve quota before each outbound attempt, in a short DB transaction."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    async def reserve_request(
        self,
        *,
        provider: str,
        request_key_hash: str,
        request_units: int,
        bytes_reserved: int = 0,
        now: datetime | None = None,
    ) -> Reservation:
        return await asyncio.to_thread(
            self._reserve_sync,
            provider,
            request_key_hash,
            request_units,
            bytes_reserved,
            now or datetime.now(UTC),
        )

    def _reserve_sync(
        self,
        provider: str,
        request_key_hash: str,
        request_units: int,
        bytes_reserved: int,
        now: datetime,
    ) -> Reservation:
        policy = POLICIES.get(provider)
        if policy is None:
            return Reservation(
                False,
                retry_at=now + timedelta(minutes=5),
                reason="unconfigured provider",
            )
        if request_units <= 0 or bytes_reserved < 0:
            return Reservation(
                False,
                retry_at=now + timedelta(minutes=5),
                reason="invalid request cost",
            )
        if any(request_units > window.units for window in policy.windows):
            return Reservation(
                False,
                retry_at=now + timedelta(minutes=5),
                reason="request cost exceeds provider window ceiling",
            )
        if policy.max_bytes_per_day and bytes_reserved > policy.max_bytes_per_day:
            return Reservation(
                False,
                retry_at=now + timedelta(days=1),
                reason="request byte reservation exceeds provider daily ceiling",
            )

        session = self._session_factory()
        try:
            session.execute(
                insert(provider_budget_state)
                .values(provider=provider, updated_at=now)
                .on_conflict_do_nothing(index_elements=[provider_budget_state.c.provider])
            )
            state = (
                session.execute(
                    select(provider_budget_state)
                    .where(provider_budget_state.c.provider == provider)
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            cooldown_until = state["cooldown_until"]
            if cooldown_until is not None and cooldown_until > now:
                session.rollback()
                return Reservation(False, retry_at=cooldown_until, reason=state["cooldown_reason"])

            usage_cutoff_seconds = max(
                max((window.seconds for window in policy.windows), default=0),
                86400 if policy.max_bytes_per_day else 0,
                policy.in_flight_lease_seconds if policy.max_in_flight else 0,
            )
            reserved_at = provider_request_usage.c.reserved_at
            columns = [
                func.coalesce(
                    func.sum(
                        case(
                            (
                                reserved_at >= now - timedelta(seconds=window.seconds),
                                provider_request_usage.c.request_units,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label(f"units_{window.seconds}")
                for window in policy.windows
            ]
            if policy.max_bytes_per_day:
                columns.append(
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    reserved_at >= now - timedelta(days=1),
                                    provider_request_usage.c.bytes_reserved,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("bytes_24h")
                )
            if policy.max_in_flight and bytes_reserved > 0:
                active_condition = and_(
                    provider_request_usage.c.completed_at.is_(None),
                    reserved_at >= now - timedelta(seconds=policy.in_flight_lease_seconds),
                    provider_request_usage.c.bytes_reserved > 0,
                )
                columns.extend(
                    [
                        func.count(
                            case((active_condition, provider_request_usage.c.id), else_=None)
                        ).label("in_flight_count"),
                        func.min(case((active_condition, reserved_at), else_=None)).label(
                            "in_flight_oldest"
                        ),
                    ]
                )
            aggregate = (
                session.execute(
                    select(*columns).where(
                        provider_request_usage.c.provider == provider,
                        reserved_at >= now - timedelta(seconds=usage_cutoff_seconds),
                    )
                )
                .mappings()
                .one()
            )

            retry_times: list[datetime] = []
            quota_retry_times: list[datetime] = []
            for window in policy.windows:
                cutoff = now - timedelta(seconds=window.seconds)
                used = int(aggregate[f"units_{window.seconds}"] or 0)
                if used + request_units > window.units:
                    available_at = self._capacity_available_at(
                        session,
                        provider=provider,
                        cutoff=cutoff,
                        units_to_expire=used + request_units - window.units,
                        window_seconds=window.seconds,
                        units_column=provider_request_usage.c.request_units,
                        now=now,
                    )
                    retry_times.append(available_at)
                    quota_retry_times.append(available_at)

            if policy.max_bytes_per_day and bytes_reserved:
                since = now - timedelta(days=1)
                used_bytes = int(aggregate["bytes_24h"] or 0)
                if used_bytes + bytes_reserved > policy.max_bytes_per_day:
                    available_at = self._capacity_available_at(
                        session,
                        provider=provider,
                        cutoff=since,
                        units_to_expire=used_bytes + bytes_reserved - policy.max_bytes_per_day,
                        window_seconds=86400,
                        units_column=provider_request_usage.c.bytes_reserved,
                        now=now,
                    )
                    retry_times.append(available_at)
                    quota_retry_times.append(available_at)

            if policy.max_in_flight and bytes_reserved > 0:
                active_count = int(aggregate["in_flight_count"] or 0)
                if active_count >= policy.max_in_flight:
                    retry_times.append(
                        aggregate["in_flight_oldest"]
                        + timedelta(seconds=policy.in_flight_lease_seconds)
                    )

            if retry_times:
                retry_at = max(retry_times)
                if quota_retry_times:
                    session.execute(
                        update(provider_budget_state)
                        .where(provider_budget_state.c.provider == provider)
                        .values(
                            cooldown_until=max(quota_retry_times),
                            cooldown_reason="local quota ceiling",
                            updated_at=now,
                        )
                    )
                    session.commit()
                    reason = "local quota ceiling"
                else:
                    # A completing stream releases concurrency immediately;
                    # persist no cooldown that would outlive that reservation.
                    session.rollback()
                    reason = "provider concurrency limit"
                return Reservation(False, retry_at=retry_at, reason=reason)

            row = session.execute(
                insert(provider_request_usage)
                .values(
                    provider=provider,
                    request_key_hash=request_key_hash,
                    reserved_at=now,
                    request_units=request_units,
                    bytes_reserved=bytes_reserved,
                    outcome="in_flight",
                )
                .returning(provider_request_usage.c.id)
            ).one()
            session.execute(
                update(provider_budget_state)
                .where(provider_budget_state.c.provider == provider)
                .values(cooldown_until=None, cooldown_reason=None, updated_at=now)
            )
            session.commit()
            return Reservation(True, reservation_id=row.id)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _capacity_available_at(
        self,
        session: Any,
        *,
        provider: str,
        cutoff: datetime,
        units_to_expire: int,
        window_seconds: int,
        units_column: Any,
        now: datetime,
    ) -> datetime:
        ordered_usage = (
            select(
                provider_request_usage.c.reserved_at.label("reserved_at"),
                func.sum(units_column)
                .over(
                    order_by=(
                        provider_request_usage.c.reserved_at,
                        provider_request_usage.c.id,
                    )
                )
                .label("expired_units"),
            )
            .where(
                provider_request_usage.c.provider == provider,
                provider_request_usage.c.reserved_at >= cutoff,
            )
            .subquery()
        )
        first_expiry = session.scalar(
            select(ordered_usage.c.reserved_at)
            .where(ordered_usage.c.expired_units >= units_to_expire)
            .order_by(ordered_usage.c.reserved_at)
            .limit(1)
        )
        return (
            first_expiry + timedelta(seconds=window_seconds)
            if first_expiry is not None
            else now + timedelta(seconds=window_seconds)
        )

    async def prune_usage(self, *, older_than: datetime | None = None) -> int:
        """Remove usage rows that can no longer affect a quota window."""
        return await asyncio.to_thread(
            self._prune_sync,
            older_than or datetime.now(UTC) - timedelta(days=7),
        )

    def _prune_sync(self, older_than: datetime) -> int:
        session = self._session_factory()
        try:
            result = session.execute(
                delete(provider_request_usage).where(
                    provider_request_usage.c.reserved_at < older_than,
                    provider_request_usage.c.completed_at.is_not(None),
                )
            )
            session.commit()
            return int(result.rowcount or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    async def complete_request(
        self,
        *,
        reservation_id: int,
        status_code: int,
        bytes_used: int | None,
        cooldown_until: datetime | None = None,
        remaining_units: int | None = None,
        now: datetime | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._complete_sync,
            reservation_id,
            status_code,
            bytes_used,
            cooldown_until,
            remaining_units,
            now or datetime.now(UTC),
        )

    def _complete_sync(
        self,
        reservation_id: int,
        status_code: int,
        bytes_used: int | None,
        cooldown_until: datetime | None,
        remaining_units: int | None,
        now: datetime,
    ) -> None:
        outcome = (
            "success"
            if 200 <= status_code < 300
            else "empty"
            if status_code == 404
            else "rate_limited"
            if status_code == 429
            else "transport_error"
            if status_code == 599
            else "server_error"
            if status_code >= 500
            else "client_error"
        )
        session = self._session_factory()
        try:
            row = session.execute(
                select(provider_request_usage.c.provider)
                .where(provider_request_usage.c.id == reservation_id)
                .with_for_update()
            ).first()
            if row is None:
                return
            session.execute(
                update(provider_request_usage)
                .where(provider_request_usage.c.id == reservation_id)
                .values(
                    completed_at=now,
                    status_code=status_code,
                    outcome=outcome,
                    bytes_reserved=(
                        bytes_used
                        if bytes_used is not None
                        else provider_request_usage.c.bytes_reserved
                    ),
                    bytes_used=bytes_used,
                    cooldown_until=cooldown_until,
                    remaining_units=remaining_units,
                )
            )
            if status_code == 429:
                provider = row.provider
                state = session.execute(
                    select(provider_budget_state.c.cooldown_until)
                    .where(provider_budget_state.c.provider == provider)
                    .with_for_update()
                ).first()
                existing = state.cooldown_until if state else None
                until = (
                    max(existing or cooldown_until, cooldown_until) if cooldown_until else existing
                )
                if until is not None:
                    session.execute(
                        update(provider_budget_state)
                        .where(provider_budget_state.c.provider == provider)
                        .values(
                            cooldown_until=until,
                            cooldown_reason="upstream HTTP 429",
                            updated_at=now,
                        )
                    )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    async def budget_snapshot(self, *, now: datetime | None = None) -> dict[str, dict[str, object]]:
        return await asyncio.to_thread(self._snapshot_sync, now or datetime.now(UTC))

    def _snapshot_sync(self, now: datetime) -> dict[str, dict[str, object]]:
        session = self._session_factory()
        try:
            output: dict[str, dict[str, object]] = {}
            states = session.execute(select(provider_budget_state)).mappings().all()
            for provider, policy in POLICIES.items():
                state = next((row for row in states if row["provider"] == provider), None)
                usage: dict[str, int] = {}
                for window in policy.windows:
                    total = session.scalar(
                        select(
                            func.coalesce(func.sum(provider_request_usage.c.request_units), 0)
                        ).where(
                            provider_request_usage.c.provider == provider,
                            provider_request_usage.c.reserved_at
                            >= now - timedelta(seconds=window.seconds),
                        )
                    )
                    usage[f"{window.seconds}s"] = int(total or 0)
                bytes_used = 0
                if policy.max_bytes_per_day:
                    bytes_used = int(
                        session.scalar(
                            select(
                                func.coalesce(func.sum(provider_request_usage.c.bytes_reserved), 0)
                            ).where(
                                provider_request_usage.c.provider == provider,
                                provider_request_usage.c.reserved_at >= now - timedelta(days=1),
                            )
                        )
                        or 0
                    )
                output[provider] = {
                    "usage": usage,
                    "limits": {f"{window.seconds}s": window.units for window in policy.windows},
                    "cooldown_until": state["cooldown_until"] if state else None,
                    "cooldown_reason": state["cooldown_reason"] if state else None,
                    "bytes_reserved_24h": bytes_used,
                    "bytes_limit_24h": policy.max_bytes_per_day,
                }
            return output
        finally:
            session.close()
