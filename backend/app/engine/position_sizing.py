from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from .models import InstrumentMetadata, PositionSizeResult

TARGET_RISK_USD = Decimal("100")
ZERO = Decimal("0")


def _metadata_is_valid(instrument: InstrumentMetadata) -> bool:
    values = (
        instrument.tick_size,
        instrument.tick_value_usd,
        instrument.conversion_rate_to_usd,
        instrument.contract_min,
        instrument.contract_max,
        instrument.contract_step,
    )
    return all(value is not None and value > ZERO for value in values)


def calculate_position_size(
    instrument: InstrumentMetadata,
    stop_distance: Decimal,
) -> PositionSizeResult:
    if stop_distance <= ZERO or not _metadata_is_valid(instrument):
        return PositionSizeResult(
            target_risk_usd=TARGET_RISK_USD,
            monetary_loss_per_one_contract=None,
            raw_size=None,
            display_size=None,
            contract_status="METADATA_UNAVAILABLE",
            reason_code="METADATA_UNAVAILABLE",
        )

    tick_size = instrument.tick_size
    tick_value = instrument.tick_value_usd
    conversion = instrument.conversion_rate_to_usd
    minimum = instrument.contract_min
    maximum = instrument.contract_max
    step = instrument.contract_step
    assert tick_size is not None
    assert tick_value is not None
    assert conversion is not None
    assert minimum is not None
    assert maximum is not None
    assert step is not None

    monetary_loss = (stop_distance / tick_size) * tick_value * conversion
    if monetary_loss <= ZERO:
        return PositionSizeResult(
            target_risk_usd=TARGET_RISK_USD,
            monetary_loss_per_one_contract=monetary_loss,
            raw_size=None,
            display_size=None,
            contract_status="METADATA_UNAVAILABLE",
            reason_code="METADATA_UNAVAILABLE",
        )

    raw_size = TARGET_RISK_USD / monetary_loss
    if raw_size < minimum:
        return PositionSizeResult(
            target_risk_usd=TARGET_RISK_USD,
            monetary_loss_per_one_contract=monetary_loss,
            raw_size=raw_size,
            display_size=None,
            contract_status="BELOW_PROVIDER_MINIMUM",
            reason_code="BELOW_PROVIDER_MINIMUM",
        )

    capped = min(raw_size, maximum)
    display_size = (capped / step).to_integral_value(rounding=ROUND_DOWN) * step
    if display_size < minimum:
        return PositionSizeResult(
            target_risk_usd=TARGET_RISK_USD,
            monetary_loss_per_one_contract=monetary_loss,
            raw_size=raw_size,
            display_size=None,
            contract_status="BELOW_PROVIDER_MINIMUM",
            reason_code="BELOW_PROVIDER_MINIMUM",
        )
    if display_size * monetary_loss > TARGET_RISK_USD:
        raise ArithmeticError("rounded contract size exceeds USD 100 target risk")

    return PositionSizeResult(
        target_risk_usd=TARGET_RISK_USD,
        monetary_loss_per_one_contract=monetary_loss,
        raw_size=raw_size,
        display_size=display_size,
        contract_status="VALID",
        reason_code="CONTRACT_SIZE_VALID",
    )
