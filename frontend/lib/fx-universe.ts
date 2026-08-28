/**
 * Client FX universe display ordering.
 * Groups first, alphabetical within each group.
 */

export const FX_GROUP_ORDER: Record<string, number> = {
  // MAJORS
  AUD_USD: 10,
  EUR_USD: 11,
  GBP_USD: 12,
  NZD_USD: 13,
  USD_CAD: 14,
  USD_CHF: 15,
  USD_JPY: 16,
  // US DOLLAR BASKET (deferred special case — displayed in FX section if supported)
  // Placeholder: if basket is onboarded as INDEX_DXY or similar, its order is 20
  DXY: 20,
  US_DOLLAR_BASKET: 20,
  INDEX_US_DOLLAR_BASKET: 20,
  // JPY CROSSES
  AUD_JPY: 30,
  CAD_JPY: 31,
  EUR_JPY: 32,
  GBP_JPY: 33,
  NZD_JPY: 34,
  // OTHER FX PAIRS
  AUD_CAD: 40,
  EUR_AUD: 41,
  EUR_CAD: 42,
  EUR_CHF: 43,
  EUR_GBP: 44,
  GBP_AUD: 45,
  GBP_CAD: 46,
  GBP_CHF: 47,
  NZD_CAD: 48,
};

export const FX_GROUP_LABELS: Record<number, string> = {
  10: "MAJORS",
  20: "US DOLLAR BASKET",
  30: "JPY CROSSES",
  40: "OTHER FX PAIRS",
};

export function fxGroupFor(instrumentId: string): number {
  const order = FX_GROUP_ORDER[instrumentId];
  if (order === undefined) return 99;
  if (order < 20) return 10;
  if (order < 30) return 20;
  if (order < 40) return 30;
  return 40;
}

export function fxDisplayOrder(instrumentId: string): number {
  return FX_GROUP_ORDER[instrumentId] ?? 999;
}

export function sortFxUniverse<T extends { instrument_id: string }>(instruments: T[]): T[] {
  return [...instruments].sort((a, b) => {
    const ga = fxGroupFor(a.instrument_id);
    const gb = fxGroupFor(b.instrument_id);
    if (ga !== gb) return ga - gb;
    const oa = fxDisplayOrder(a.instrument_id);
    const ob = fxDisplayOrder(b.instrument_id);
    if (oa !== ob) return oa - ob;
    return a.instrument_id.localeCompare(b.instrument_id);
  });
}

export const CLIENT_FX_UNIVERSE_DISPLAY_ORDER: string[] = [
  "AUD_USD",
  "EUR_USD",
  "GBP_USD",
  "NZD_USD",
  "USD_CAD",
  "USD_CHF",
  "USD_JPY",
  "US_DOLLAR_BASKET",
  "AUD_JPY",
  "CAD_JPY",
  "EUR_JPY",
  "GBP_JPY",
  "NZD_JPY",
  "AUD_CAD",
  "EUR_AUD",
  "EUR_CAD",
  "EUR_CHF",
  "EUR_GBP",
  "GBP_AUD",
  "GBP_CAD",
  "GBP_CHF",
  "NZD_CAD",
];
