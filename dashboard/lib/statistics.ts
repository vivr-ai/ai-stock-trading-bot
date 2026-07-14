// Lightweight statistical primitives for Pattern Discovery. No external
// stats library is worth adding for this - everything here is a standard,
// well-known formula. Two simplifications are made deliberately and
// disclosed wherever they're used:
//   1. p-values from the t-distribution are approximated using the normal
//      distribution (via the Fisher z-transform for correlations, and
//      directly for two-sample tests). This is a standard, well-accepted
//      approximation once each group has at least ~30 observations - which
//      is also this project's minimum sample size for reporting a finding
//      at all, so the approximation is never relied on below its safe range.
//   2. All tests are two-tailed - Pattern Discovery cares whether a factor
//      makes a difference in either direction, not just improvement.

/** Abramowitz-Stegun approximation of the error function (max error ~1.5e-7). */
function erf(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
  const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
  const t = 1 / (1 + p * ax);
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-ax * ax);
  return sign * y;
}

export function normalCdf(z: number): number {
  return 0.5 * (1 + erf(z / Math.SQRT2));
}

/** Two-tailed p-value from a z-score under the standard normal. */
export function twoTailedPValue(z: number): number {
  return 2 * (1 - normalCdf(Math.abs(z)));
}

export type ProportionTestResult = {
  proportionA: number;
  proportionB: number;
  diffPct: number; // percentage points, A - B
  z: number;
  pValue: number;
};

/** Two-proportion z-test (pooled), e.g. comparing win rates between two groups. */
export function twoProportionZTest(
  successesA: number, nA: number, successesB: number, nB: number
): ProportionTestResult {
  const pA = successesA / nA;
  const pB = successesB / nB;
  const pPooled = (successesA + successesB) / (nA + nB);
  const se = Math.sqrt(pPooled * (1 - pPooled) * (1 / nA + 1 / nB));
  const z = se > 0 ? (pA - pB) / se : 0;
  return { proportionA: pA, proportionB: pB, diffPct: (pA - pB) * 100, z, pValue: twoTailedPValue(z) };
}

export function pearsonCorrelation(xs: number[], ys: number[]): number | null {
  const n = xs.length;
  if (n !== ys.length || n < 3) return null;
  const meanX = xs.reduce((a, b) => a + b, 0) / n;
  const meanY = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0, denX = 0, denY = 0;
  for (let i = 0; i < n; i++) {
    const dx = xs[i] - meanX;
    const dy = ys[i] - meanY;
    num += dx * dy;
    denX += dx * dx;
    denY += dy * dy;
  }
  const den = Math.sqrt(denX * denY);
  if (den === 0) return null;
  return num / den;
}

/** p-value for a Pearson correlation via the Fisher z-transform. */
export function correlationPValue(r: number, n: number): number | null {
  if (n < 4 || Math.abs(r) >= 1) return null;
  const fisherZ = 0.5 * Math.log((1 + r) / (1 - r));
  const se = 1 / Math.sqrt(n - 3);
  const z = fisherZ / se;
  return twoTailedPValue(z);
}

export function mean(values: number[]): number | null {
  if (values.length === 0) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function variance(values: number[], m: number): number {
  if (values.length < 2) return 0;
  return values.reduce((a, v) => a + (v - m) ** 2, 0) / (values.length - 1);
}

export type MeanDiffTestResult = {
  meanA: number;
  meanB: number;
  diff: number;
  z: number;
  pValue: number;
};

/** Welch's t-test for two independent samples, p-value approximated via the
 * normal distribution (safe once both groups are at or above this project's
 * n>=30 minimum-sample bar). */
export function welchMeanDiffTest(a: number[], b: number[]): MeanDiffTestResult | null {
  if (a.length < 2 || b.length < 2) return null;
  const meanA = mean(a)!;
  const meanB = mean(b)!;
  const varA = variance(a, meanA);
  const varB = variance(b, meanB);
  const se = Math.sqrt(varA / a.length + varB / b.length);
  const z = se > 0 ? (meanA - meanB) / se : 0;
  return { meanA, meanB, diff: meanA - meanB, z, pValue: twoTailedPValue(z) };
}
