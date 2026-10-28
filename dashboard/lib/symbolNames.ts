// Company names for bot/universe/static_universe.py's STATIC_UNIVERSE, so
// the dashboard can show "V — Visa Inc." instead of a bare ticker. Mirrors
// the same pattern as sectors.ts: a hand-maintained map for the bot's fixed,
// curated universe, not a live lookup. Symbols not listed here (e.g. if the
// universe provider is ever switched away from "static") just render with
// no name shown - nameFor() returns undefined rather than guessing.
const SYMBOL_NAMES: Record<string, string> = {
  // Mega-cap tech
  AAPL: "Apple",
  MSFT: "Microsoft",
  NVDA: "NVIDIA",
  GOOGL: "Alphabet (Google)",
  AMZN: "Amazon.com",
  META: "Meta Platforms",
  TSLA: "Tesla",
  AVGO: "Broadcom",
  ORCL: "Oracle",
  AMD: "Advanced Micro Devices",
  ADBE: "Adobe",
  CRM: "Salesforce",
  INTC: "Intel",
  CSCO: "Cisco Systems",
  QCOM: "Qualcomm",
  TXN: "Texas Instruments",
  MU: "Micron Technology",
  PLTR: "Palantir Technologies",
  NFLX: "Netflix",
  IBM: "IBM",
  // Financials
  JPM: "JPMorgan Chase",
  BAC: "Bank of America",
  WFC: "Wells Fargo",
  GS: "Goldman Sachs",
  MS: "Morgan Stanley",
  C: "Citigroup",
  BLK: "BlackRock",
  SCHW: "Charles Schwab",
  AXP: "American Express",
  V: "Visa",
  MA: "Mastercard",
  PYPL: "PayPal Holdings",
  // Healthcare
  UNH: "UnitedHealth Group",
  JNJ: "Johnson & Johnson",
  LLY: "Eli Lilly",
  PFE: "Pfizer",
  MRK: "Merck & Co.",
  ABBV: "AbbVie",
  TMO: "Thermo Fisher Scientific",
  ABT: "Abbott Laboratories",
  // Consumer / industrial / energy
  WMT: "Walmart",
  COST: "Costco Wholesale",
  HD: "Home Depot",
  MCD: "McDonald's",
  NKE: "Nike",
  SBUX: "Starbucks",
  DIS: "Walt Disney",
  KO: "Coca-Cola",
  PEP: "PepsiCo",
  PG: "Procter & Gamble",
  XOM: "Exxon Mobil",
  CVX: "Chevron",
  BA: "Boeing",
  CAT: "Caterpillar",
  GE: "General Electric",
  F: "Ford Motor",
  GM: "General Motors",
  UBER: "Uber Technologies",
  COIN: "Coinbase Global",
  T: "AT&T",
};

export function nameFor(symbol: string): string | undefined {
  return SYMBOL_NAMES[symbol];
}
