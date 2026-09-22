"""Collection and evaluation health are separate public observations."""

# A blocked evaluation only invalidates the Filter diagnostic when the block is
# caused by a dependency the Filter itself requires. Signal-only failures must
# not make a healthy filter look stale.
FILTER_DEPENDENCY_MARKERS = (
    "MISSING_DAILY_CANDLE",
    "MISSING_WEEKLY_CANDLE",
    "INSUFFICIENT_COMPLETED_D1",
    "INSUFFICIENT_COMPLETED_W1",
    "SHARE_FILTER_REQUIRED_H1_MISSING_OR_INVALID",
    "FILTER_DEPENDENCY",
    "DAILY_FILTER",
    "WEEKLY_FILTER",
)


def _is_filter_dependency_failure(reason):
    return bool(reason) and any(marker in reason for marker in FILTER_DEPENDENCY_MARKERS)


def attach_collection_status(snapshot, runtime):
    if runtime is None:
        return snapshot
    details = runtime.status().get("collection_instruments", {})
    rows = []
    for row in snapshot.instruments:
        detail = details.get(row.instrument_id)
        if detail is None:
            rows.append(row)
            continue
        mode = detail.get("evaluation_modes", {}).get(snapshot.active_filter_mode)
        if mode is not None:
            detail = {**detail, **mode}
        failure_reason = detail.get("failure_reason")
        update = {
            "collection": detail.get(
                "collection",
                {"state": "BLOCKED", "failure_reason": failure_reason},
            ),
            "evaluation_freshness": detail.get("evaluation_freshness", "STALE"),
        }
        if update["evaluation_freshness"] != "CURRENT":
            update.update(
                stale=True,
                data_status="STALE",
                provider_health="STALE",
                latest_error_summary=failure_reason
                or "Waiting for current calculation inputs",
            )
            current_filter = getattr(row, "current_filter", None)
            if (
                _is_filter_dependency_failure(failure_reason)
                and current_filter is not None
                and getattr(current_filter, "filter_freshness", None) == "CURRENT"
            ):
                update["current_filter"] = current_filter.model_copy(
                    update={
                        "filter_freshness": "STALE",
                        "stale_reason": failure_reason,
                    }
                )
        elif mode is not None:
            update.update(stale=False, data_status="HEALTHY", provider_health="HEALTHY", latest_error_summary=None)
        rows.append(row.model_copy(update=update))
    return snapshot.model_copy(update={"instruments": rows})
