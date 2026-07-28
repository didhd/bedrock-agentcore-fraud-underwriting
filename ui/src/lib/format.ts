/**
 * Formatters. The single most important function in this file is `dash()`.
 *
 * A null measurement renders as an em dash, always. No `?? 0`, no "~", no
 * rounded-up guess. Everything the customer's executives see either came off a
 * real measurement in this run or reads as an em dash.
 */

export const EM_DASH = "—"

export function dash(value: unknown): string {
  return value === null || value === undefined || value === "" ? EM_DASH : String(value)
}

export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return EM_DASH
  if (value < 1000) return `${Math.round(value)} ms`
  return `${(value / 1000).toFixed(2)} s`
}

export function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return EM_DASH
  return `${(value / 1000).toFixed(2)} s`
}

export function integer(value: number | null | undefined): string {
  if (value === null || value === undefined) return EM_DASH
  return value.toLocaleString("en-US")
}

/**
 * Money. Per-application cost is fractions of a cent, so small values keep six
 * decimals rather than collapsing to "$0.00" -- rounding a real measurement to
 * zero is its own kind of dishonesty.
 */
export function usd(value: number | null | undefined): string {
  if (value === null || value === undefined) return EM_DASH
  if (value === 0) return "$0"
  if (Math.abs(value) < 0.01) return `$${value.toFixed(6)}`
  if (Math.abs(value) < 1) return `$${value.toFixed(4)}`
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function money0(value: number | null | undefined): string {
  if (value === null || value === undefined) return EM_DASH
  return `$${Math.round(value).toLocaleString("en-US")}`
}

export function rateLabel(
  rate: { input_per_mtok?: number; output_per_mtok?: number; cache_read_per_mtok?: number } | null,
): string {
  if (!rate || rate.input_per_mtok === undefined || rate.output_per_mtok === undefined) {
    return EM_DASH
  }
  const base = `$${rate.input_per_mtok} in / $${rate.output_per_mtok} out per 1M`
  return rate.cache_read_per_mtok !== undefined
    ? `${base} - $${rate.cache_read_per_mtok} cache read`
    : base
}

/** Trim the Bedrock inference-profile prefix so a model id fits in a card. */
export function shortModel(modelId: string | null): string {
  if (!modelId) return EM_DASH
  return modelId.replace(/^(us|eu|apac|global)\.anthropic\./, "").replace(/-v\d+:\d+$/, "")
}

/** Which region prefix a model id carries -- the us.* 1.10x multiplier matters. */
export function modelScope(modelId: string | null): string | null {
  if (!modelId) return null
  const match = /^(us|eu|apac|global)\./.exec(modelId)
  return match ? match[1] : null
}
