"""Collection and evaluation health are separate public observations."""


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
        update = {
            "collection": detail.get(
                "collection",
                {"state": "BLOCKED", "failure_reason": detail.get("failure_reason")},
            ),
            "evaluation_freshness": detail.get("evaluation_freshness", "STALE"),
        }
        if update["evaluation_freshness"] != "CURRENT":
            update.update(
                stale=True,
                data_status="STALE",
                provider_health="STALE",
                latest_error_summary=detail.get("failure_reason")
                or "Waiting for current calculation inputs",
            )
        rows.append(row.model_copy(update=update))
    return snapshot.model_copy(update={"instruments": rows})
