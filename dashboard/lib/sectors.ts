// Mirrors bot/universe/static_universe.py's SECTORS map exactly, so the Risk
// Dashboard's sector exposure figures line up with the bot's own "max
// positions per sector" cap. Symbols not listed here resolve to "unknown"
// and are exempt from that cap, same as on the bot side.
const SECTORS: Record<string, string> = {
  // Semiconductors (highly correlated as a group)
  NVDA: "semis", AMD: "semis", AVGO: "semis", INTC: "semis",
  QCOM: "semis", TXN: "semis", MU: "semis",
  // Software / internet / mega-cap tech
  AAPL: "tech", MSFT: "tech", GOOGL: "tech", AMZN: "tech",
  META: "tech", ORCL: "tech", ADBE: "tech", CRM: "tech",
  CSCO: "tech", PLTR: "tech", NFLX: "tech", IBM: "tech",
  // Financials
  JPM: "financials", BAC: "financials", WFC: "financials", GS: "financials",
  MS: "financials", C: "financials", BLK: "financials", SCHW: "financials",
  AXP: "financials", V: "financials", MA: "financials", PYPL: "financials",
  COIN: "financials",
  // Healthcare
  UNH: "healthcare", JNJ: "healthcare", LLY: "healthcare", PFE: "healthcare",
  MRK: "healthcare", ABBV: "healthcare", TMO: "healthcare", ABT: "healthcare",
  // Consumer
  WMT: "consumer", COST: "consumer", HD: "consumer", MCD: "consumer",
  NKE: "consumer", SBUX: "consumer", DIS: "consumer", KO: "consumer",
  PEP: "consumer", PG: "consumer", TSLA: "consumer", UBER: "consumer",
  // Energy / industrials / autos / telecom
  XOM: "energy", CVX: "energy", BA: "industrials", CAT: "industrials",
  GE: "industrials", F: "autos", GM: "autos", T: "telecom",
};

export function sectorOf(symbol: string): string {
  return SECTORS[symbol] ?? "unknown";
}

export const SECTOR_LABELS: Record<string, string> = {
  semis: "Semiconductors",
  tech: "Tech / Internet",
  financials: "Financials",
  healthcare: "Healthcare",
  consumer: "Consumer",
  energy: "Energy",
  industrials: "Industrials",
  autos: "Autos",
  telecom: "Telecom",
  unknown: "Other / unmapped",
};
