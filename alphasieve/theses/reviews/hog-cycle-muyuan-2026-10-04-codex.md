# Thesis review — 牧原股份猪周期估值推演复核

Review date: 2026-10-04  
Thesis as stated: researching; snapshot `as_of: 2026-09-14`  
Asset: 002714.SZ 牧原股份

## Findings

| Claim | Verdict | Evidence with URL | Impact on valuation |
|---|---|---|---|
| 2026 年公司商品猪出栏指引为 7,500–8,100 万头，取中值 7,800 万头 | holds | 公司 2026 年业绩说明会记录披露“预计全年销售商品猪 7,500 万头–8,100 万头”：[业绩说明会记录](https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?CompanyCode=80198868&gather=1&id=12097210)。公司 2026H1 报告也确认上半年销售商品猪 3,861.5 万头：[中报 PDF](https://static.cninfo.com.cn/finalpage/2026-08-21/1225485220.PDF) | 7,800 万头是区间中点，作为情景假设成立；它是管理层指引而非确定结果，仍应保留区间敏感性。 |
| 2026 年 6 月完全成本约 11.7 元/kg，全年目标 11.5 元/kg | holds | 公司 2026H1 报告明确披露 6 月完全成本约 11.7 元/kg、全年平均目标 11.5 元/kg：[中报 PDF](https://static.cninfo.com.cn/finalpage/2026-08-21/1225485220.PDF) | 这支持公司降本趋势，但不能直接证明 2027E 11.2 元/kg；若 11.2 不能由可复核的 2027 预测报告支持，广发情景的利润和 41.99 元/股估值应视为未验证。 |
| 广发证券 2027E 猪价 14.0 元/kg、完全成本 11.2 元/kg | unverified | YAML 只引用 AlphaNotes §8，没有报告 URL、发布日期、页码或可核验原文。对“猪周期估值推演的辩证复核”的精确标题进行检索未找到公开来源；尝试的公司一手披露只给出 2026 成本目标，并未给出该 2027 预测：[中报 PDF](https://static.cninfo.com.cn/finalpage/2026-08-21/1225485220.PDF) | 这是基准情景的核心价格和成本输入。模型对猪价的敏感度为每 kg 约 ¥15.00/股（输出区间 ¥26.995–¥101.982），因此来源缺失足以改变结论。 |
| 2025 年实际完全成本约 12 元/kg，可作为修订成本下界 | holds for 2025 actual; unverified for forecast use | 公司年报披露 2025 年全年生猪养殖完全成本约 12 元/kg：[2025 年年报](https://static.cninfo.com.cn/finalpage/2026-03-28/1225042507.PDF)；但没有一手来源支持“上行期 12.0–13.5”或“扩产到 1 亿头对应 12.7–13.5”的映射。 | 修订成本情景的 86.52–101.91 元/股结果依赖未证实的成本区间；应将其标为假设区间，不能作为已验证目标价。 |
| 出栏均重取 120 kg/头，区间 120–125 kg/头 | unverified | 该数值在 YAML 中只追溯到原文档假设。公司 2026 年业绩说明会公开回答的是 2025 年商品猪销售均重“约 126 公斤”，见[业绩说明会记录](https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?CompanyCode=80198868&gather=1&id=12097210)；2026H1 年报和月度销售简报未提供可直接核验的 2026 年均重序列。 | 120 kg 可能低估头均收入和利润；模型自身 120→125 kg 的敏感度为约 +¥1.75/股，且 126 kg 已超出设定上限，需补充当期均重证据或扩展区间。 |
| 归母折算因子为 0.925 | unverified / model weakness | YAML 仅以“本报告模型”作为证据，没有由合并报表、少数股东损益、所得税、期间费用或其他业务推导的桥接。年报显示公司同时经营养殖、屠宰肉食等业务，2025 年屠宰肉食收入 452.28 亿元：[2025 年年报](https://static.cninfo.com.cn/finalpage/2026-03-28/1225042507.PDF)。 | 公式把“头均毛利 × 出栏量”直接乘一个未经解释的比例后套 PE，未证明其等于归母净利润；可能重复遗漏屠宰、税费、管理/财务费用及少数股东损益，属于估值结构性风险，可能显著高估或低估。 |
| 57.73 亿股总股本 | holds | 2026H1 年报股本期末为 5,772,996,075 股（约 57.73 亿股）：[中报 PDF](https://static.cninfo.com.cn/finalpage/2026-08-21/1225485220.PDF)；评级报告也交叉披露 2026 年 3 月股本升至 57.73 亿股：[跟踪评级报告](https://static.cninfo.com.cn/finalpage/2026-05-28/1225334235.PDF) | 股本输入可复核；仍应说明估值日是否使用期末股本，以及后续 H 股发行/回购是否会改变每股值。 |
| 2026-09-14 收盘价为 42.59 元 | unverified for primary-source SOP | YAML 未给出行情 URL。公开历史行情页面可见 2026-09-14 收盘 42.59 元：[历史行情](https://cn.investing.com/equities/muyuan-foodstuff-a-historical-data)，但这不是交易所/公司一手披露。 | 市场隐含值和“低于/高于现价”的判断依赖该数字；应补充深交所或可审计行情源，并注明复权口径。 |
| 估值公式单位正确且场景数值可复现 | holds mechanically; limited economically | `alphasieve thesis validate target.yaml` 返回 valid；`alphasieve thesis scenarios target.yaml` 输出：base/广发情景 ¥41.9927，original ¥115.3646，修订成本低/无扩产 ¥89.9844，低/扩产 ¥101.9054，高/扩产 ¥86.5235。 | 单位上（元/kg × kg/头 × 亿头 ÷ 亿股）可闭合；但经济含义仍受未验证的猪价、成本、均重和归母折算限制。 |
| 现价对应模型隐含猪价约 14.04 元/kg | holds mechanically | `alphasieve thesis implied target.yaml --param hog_price --target 42.59` 输出 14.039825 元/kg；计算依赖上表未验证输入和市场价。 | 可作为条件式市场检验：在当前其他参数、PE 和股本都成立时，市场价格隐含约 14.04 元/kg；不应解读为独立的猪价预测。 |
| LH2707 到期结算价的结算日为 2027-07-31 | fails as scheduled date | 大连商品交易所生猪业务细则规定最后交易日为合约月份倒数第 4 个交易日、最后交割日为其后第 3 个交易日：[DCE 生猪业务细则](https://www.dce.com.cn/dalianshangpin/fgfz/6142914/6142926/6262877/%E5%A4%A7%E8%BF%9E%E5%95%86%E5%93%81%E4%B8%9A%E5%8A%A1%E7%BB%86%E5%88%99%EF%BC%88%E6%A0%B9%E6%8D%AE2025%E5%B9%B46%E6%9C%8824%E6%97%A5%E3%80%942025%E3%80%9559%E5%8F%B7%E6%96%87%E4%BB%B6%E4%BF%AE%E6%94%B9%EF%BC%89.pdf)。2027-07-31 is a Saturday, and the exact exchange calendar was not supplied. | The proposed forecast is not currently settleable on the stated date. Replace it with the DCE-published last trading/settlement date and define whether the observation is final settlement price, last close, or a daily settlement price. |
| 2026-11-30 月度销售简报会披露“出栏均重是否超过 125 kg/头” | unverified / not operationally settleable | The cited AlphaNotes §11 is not a public URL. The company’s monthly sales bulletins disclose volume, price and revenue; the reviewed 2026H1 report does not provide a monthly average-weight field：[中报 PDF](https://static.cninfo.com.cn/finalpage/2026-08-21/1225485220.PDF)。 | A forecast cannot be scored if the named source does not publish the variable. Specify an alternative observable source or change the proposition to a disclosed volume/price metric. |
| `as_of` 2026-09-14 is the thesis snapshot | fails as version metadata | The file has `as_of: 2026-09-14` but includes a revision dated 2026-09-27 that changes the cost range. | The valuation mixes a 9/14 market snapshot with post-snapshot assumptions. Either set `as_of` to the latest incorporated evidence date or maintain a dated version of the 9/14 thesis. |

## Scorecard

| Dimension | Score | Rationale |
|---|---:|---|
| Argument structure | 2/2 | Cycle view, parameter layer and valuation layer are explicit. |
| Parameter evidence | 1/3 | Company guide, cost disclosure and share count are traceable; most model inputs rely on AlphaNotes-only C evidence without URLs or primary documents. |
| Valuation mechanics | 1/3 | Units and arithmetic are reproducible, but the 0.925 attribution factor and omission of a consolidated earnings bridge are not justified. |
| Falsifiers and forecasts | 1/2 | Falsifiers identify cost/price breakpoints, but the two proposed forecasts lack a valid exchange date or an observable monthly-weight field. |
| **Total** | **5/10** | **Researchable thesis; not ready for an evidence-backed position or target price.** |

## Required fixes before promotion

1. Attach public URLs, publication dates and page/section references for the Guangfa 2027E assumptions, revised cost range, original assumptions and 0.925 factor; otherwise downgrade those inputs to unverified assumptions.
2. Replace the attribution factor with a reconciled bridge from operating/head profit to consolidated attributable net income, including taxes, expenses, minority interest and the treatment of slaughter/meat operations.
3. Rebase the weight assumption using a dated company disclosure or extend the sensitivity beyond 125 kg/头.
4. Correct the LH2707 settlement date using the DCE calendar and redefine the settlement observable.
5. Replace the 2026-11-30 weight forecast with a variable that the cited company bulletin actually publishes.
6. Resolve the time-series inconsistency between `as_of: 2026-09-14` and the 2026-09-27 revision.
