# Review: hog-cycle-muyuan — 牧原股份猪周期估值推演复核

- Reviewed file: `target.yaml` (identical to `example.yaml`), `as_of` 2026-09-14, schema-valid (`alphasieve thesis validate`).
- Review date: 2026-10-04.
- All valuation numbers below come from `alphasieve thesis scenarios` / `alphasieve thesis implied`. Extra review-only scenarios are in `reviews/check_variants.yaml`.

## Source access (read first)

I could not reach any external source during this review:

| Tool | Attempt | Result |
|---|---|---|
| WebSearch | "牧原股份 2026年半年度报告 总股本 股"; "牧原股份 2026年 生猪出栏 指引 7500-8100万头" | Upstream error 400: tool type not supported |
| WebFetch | https://www.cninfo.com.cn/new/disclosure/stock?stockCode=002714&orgId=9900023134 (company filings) | 401, fetch model unavailable |
| WebFetch | https://quote.eastmoney.com/sz002714.html (price, share count) | 401, fetch model unavailable |
| WebFetch | https://www.dce.com.cn/ (LH contract rules) | ECONNREFUSED |

So **every external fact is marked `unverified`**. The only verdicts I could reach (`holds`/`fails`) are about internal consistency, arithmetic run through the backend, and compliance with the SOP.

## Backend results

| Item | Value | Command |
|---|---|---|
| Base (= `guangfa_2027`) | 41.99 元/股 | `scenarios target.yaml` |
| `original` (18 / 12 / 1.0亿头 / 0.925) | 115.36 元/股 | same |
| `revised_cost_low_no_expansion` (18 / 12 / 0.78) | 89.98 元/股 | same |
| `revised_cost_low_with_expansion` (18 / 12.7 / 1.0) | 101.91 元/股 | same |
| `revised_cost_high_with_expansion` (18 / 13.5 / 1.0) | 86.52 元/股 | same |
| Market price | 42.59 元 | `valuation.market_price` |
| Implied hog_price (other params at base) | **14.04 元/kg** | `implied --param hog_price` |
| Implied full_cost | 11.16 元/kg | `implied --param full_cost` |
| Implied PE | 10.14 倍 | `implied --param pe` |
| Implied slaughter_volume | 0.791 亿头 | `implied --param slaughter_volume` |
| Implied hog_price if full_cost = 12.0 (the thesis's own up-cycle cost floor) | **14.84 元/kg** | `implied check_variants.yaml --param hog_price` |
| `original` without the 归母 factor | 124.72 元/股 | `scenarios check_variants.yaml` |
| Falsifier boundary 16 / 13, 0.78亿头 | 44.99 元/股 | same |
| Falsifier boundary 16 / 13, 1.0亿头 | 57.68 元/股 | same |
| GF 2027 case with weight 125 kg | 43.74 元/股 | same |
| Sensitivity ranking (base) | hog_price ±60.0 > full_cost −34.5 > pe +31.1 > volume +11.8 > weight +1.7 > factor ±1.1 | `scenarios target.yaml` |

**What the market is pricing:** at 42.59 元 the stock prices almost exactly the 广发 2027E case: 14.04 元/kg hog price vs. an 11.2 cost at 10x PE. The thesis base case has **no edge over the market** (41.99 vs. 42.59). All the upside comes from the 18 元/kg scenarios, and the 1 亿头 volume in those scenarios is contradicted by the thesis's own D-grade evidence (see F7).

## Findings

| # | Claim | Verdict | Evidence / URL tried | Impact on valuation |
|---|---|---|---|---|
| F1 | 总股本 57.73 亿股 (2026-06-30), grade A | unverified | Only cited via AlphaNotes, with no URL. Tried WebSearch (H1 report query) and https://quote.eastmoney.com/sz002714.html; both failed. | Value scales with 1/shares; it is a divisor in every scenario. A secondary report should not be graded A. Check against the 2026 半年报 on cninfo, including any H-share issuance or buybacks. |
| F2 | 2026 出栏指引 7500–8100 万头, grade A | unverified | Cited via AlphaNotes, no URL. Tried WebSearch and cninfo; both failed. | Sets the low end of volume (0.75). Moving volume 0.75→1.0 changes value from −1.6 to +11.8 元/股 around base. |
| F3 | 2026-09-14 close 42.59 元, grade A | unverified | No URL. Tried eastmoney; failed. | This is the anchor for every implied value. If wrong, all implied numbers shift. |
| F4 | 广发 2027E hog price 14.0 元/kg / cost 11.2 元/kg | unverified | The broker report is not linked; it is cited only through AlphaNotes §2.1/§8. | This is the base case. Hog price is the most sensitive input (±60 元/股 over 13–18). |
| F5 | Original-doc assumptions (18 元/kg, 1亿头, 120 kg, 10x, cost 12) | holds as reported assumptions, but the 12.0 cost has no evidence | The `original` scenario uses `full_cost: 12.0`, but no evidence item carries 12.0. `gf_cost` is 11.2, and `revised_cost` is a 12.0–13.5 range with no value. | The 115.36 figure rests on an unsourced cost. Add an `original_cost` evidence item. |
| F6 | Revision: original "约 120 元" was not adjusted for 归母; 115.36 after the 0.925 factor | holds (loosely) | Backend gives 124.72 without the factor and 115.36 with it. | "约 120" understates the original figure by about 5 元. Use 124.7 so the revision reproduces exactly. |
| F7 | `slaughter_volume.high` = 1.0 亿头 | fails as a sourced range | The thesis's own `missing_volume_statement` (grade D) says no management statement supports 1 亿头, yet that D item is not attached to the parameter. The only support is `original_volume` (C, the original doc). | Three of five scenarios (`original`, both `with_expansion`) depend on an input marked searched-and-not-found. Attach the D item, or treat 1.0 as a hypothesis rather than a range bound. |
| F8 | Cross-period consistency (argument line 1: "跨期猪价须锁定同一口径") | fails | `guangfa_2027` combines **2027E** price and cost with **2026** volume guidance (`guide_midpoint`). | The base case violates the thesis's own rule. Use a 2027 volume estimate, or label the case as a mixed-period proxy. |
| F9 | Range bounds `hog_price.low` 13, `pe.high` 17.4, `average_weight.high` 125, `attributable_factor` 0.90/0.95 | fails (no evidence) | None of these bounds has an evidence item. | `pe.high` 17.4 alone adds +31.1 元/股, the third-largest swing, with no source. Weight 125 → 43.74. |
| F10 | 归母折算 0.925 ("本报告模型") | unverified | Not derived from reported 归母净利润 / 净利润; no primary source. Tried cninfo; failed. | Small effect (±1.1 元/股), but it should come from the annual report's minority-interest split. |
| F11 | 均重 120 kg/头 | unverified | Only the original doc (C). Muyuan's monthly sales briefs would let you back out weight from revenue / (price × heads), but I could not fetch them. | Low sensitivity (+1.7 元/股 at 125 kg). |
| F12 | Corn falsifier: 2600 元/吨 is "about 10% above 2372 元/吨" | partially holds | The 2600/2372 ratio is about +9.6%, consistent with "一成". But the 2372 baseline has no evidence item, the 卓创/发改委 series has no URL, and the falsifier protects a **12 元/kg** cost, not the 11.2 base. | Misaligned with the base case. At cost 12.0, market-implied hog price rises to 14.84, so the falsifier should be calibrated against the base. |
| F13 | Falsifier "2027 猪价 >16 且成本 >13 → 撤销头均利润 720 元" | fails as a base-case falsifier | 720 元/头 is the original-doc margin ((18−12)×120). The base-case margin is (14−11.2)×120. At the 16/13 boundary the backend gives 44.99 元/股, which is **above** market. | Neither falsifier targets the base case. Add a downside falsifier, e.g. a 2027 price or cost path that pushes value below 42.59 (implied price 14.04 at cost 11.2). |
| F14 | Argument line 1: 能繁母猪去化 drives later hog prices | unverified | No evidence item covers sow inventory (农业农村部 data). | The cycle premise behind the 18 元/kg scenarios is unsupported. |
| F15 | Forecast: LH2707 到期结算价 > 16 元/kg, settle 2027-07-31 | unverified | Tried https://www.dce.com.cn/ for contract rules; connection refused. The thesis itself flags the date for DCE calendar review. | "到期结算价" needs a precise definition (last-trading-day settlement vs. delivery settlement), and the date must match the DCE calendar. |
| F16 | Forecast: monthly brief shows 均重 > 125 kg, settle 2026-11-30 | unverified | Could not fetch briefs from cninfo. As far as I know, the briefs report heads, revenue and price, not weight directly. | Define the resolution rule (which month, and weight derived as revenue ÷ price ÷ heads). |
| F17 | SOP: URL and access date for every figure; grade A means primary source | fails | None of the 14 evidence items has a URL. Every item is sourced to the AlphaNotes report, including the three graded A. | Evidence quality is overstated. At most, items should be C until primary filings are linked. |
| F18 | Units of the formula | holds | (元/kg) × (kg/头) × (亿头) = 亿元 profit; × PE ÷ 亿股 = 元/股. | Correct. The model does assume all profit comes from hog farming at one year's margin under a flat PE. That is a known cyclical-PE caveat, not stated in the thesis. |
| F19 | `position_limit` 0.0 with status `researching` | holds | The revision note explains it. | None. |

## Scorecard

| Dimension | Score (0–5) | Notes |
|---|---|---|
| Argument structure | 3 | Cycle → parameters → valuation is clear. The cycle premise (F14) is unevidenced, and the base case breaks the same-period rule (F8). |
| Evidence sourcing | 1 | No URLs. All items come from a single secondary report. A grades are not justified (F17). External verification was blocked in this review. |
| Parameter ranges | 2 | Several bounds have no source (F9). The 1 亿头 upper bound conflicts with D evidence (F7). The 12.0 cost has no evidence (F5). |
| Valuation mechanics | 4 | Formula and units are correct and reproduce in the backend. Revision 120 vs 124.7 is minor (F6). |
| Market-implied analysis | 2 | Not in the thesis. Backend shows the market already prices the base case (implied 14.04 元/kg). |
| Falsifiers | 2 | Both target the original-doc 12 元 / 720 元 framing, not the 14 / 11.2 base (F12, F13). |
| Forecasts | 3 | Two settleable propositions with thresholds. Settlement definitions and dates need tightening (F15, F16). |
| **Overall** | **2.4 / 5** | Usable as a scenario frame, not yet as an investable thesis. |

## Required before status can move past `researching`

1. Replace AlphaNotes-only citations with primary URLs: the cninfo 2026 半年报 (share count), the 出栏指引 announcement, monthly sales briefs (weight), the 广发 report, a price-data source for the 09-14 close, and DCE contract rules. Re-grade afterwards.
2. Source or remove the unsupported bounds: 13 元/kg, PE 17.4, 125 kg, factor 0.90/0.95, and cost 12.0.
3. Attach `missing_volume_statement` (D) to `slaughter_volume`, and use a 2027 volume estimate in `guangfa_2027`.
4. Add a base-case falsifier tied to the market-implied 14.04 元/kg (cost 11.2), or 14.84 元/kg if cost is 12.0.
5. Define forecast settlement precisely: LH2707 settlement-price type and DCE date, and the weight-derivation rule and reporting month.
