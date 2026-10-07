# 公司公告首版管线

状态：已实现；本机公告从 2026-07 起，dev 区间只回补了 2018-06 三天试点与 2019–2022（D-43）。2026-10-07 核对。

来源：巨潮资讯公开 `POST https://www.cninfo.com.cn/new/hisAnnouncement/query`，PDF 来自 `https://static.cninfo.com.cn/`。`data sync --dataset cninfo_announcements --start YYYY-MM-DD --end YYYY-MM-DD` 按公告日回补，`data daily-update` 重查最近四个自然日。每日日表位于 `raw/cninfo/announcements/YYYY-MM-DD.parquet`，只含元数据；`announce list/show/watch` 读本地数据，`announce fetch ID` 才下载 PDF 至 `raw/cninfo/documents/ID/original.pdf` 并用新增依赖 `pypdf` 抽取 `text.json`。同步记录 `data_snapshots`，PDF 另记录 SHA-256。公告不进入研究 panel、holdout 或已有事件因子。

字段：`announcement_id`、`code`、`name`、`title`、`published_at`（北京时间、带 `+08:00`）、`published_date`、`org_id`、`pdf_url`、`adjunct_size_kb`、`source`。标题分类另加 `event_type`、`importance`、`extract_text`；属于规则提示，不是结构化事实。`text.json` 按 PDF 原页保存 `page`、`start`、`end`、`text`；`evidence_pack` 给出页码及字符区间，供后续人工或 agent 引用 `ID#page=N`。扫描版 PDF 可能没有可提取文字，首版不做 OCR 或数值抽取。

| 类型 | 标题关键词示例 | 重要性 |
|---|---|---|
| `convertible_redemption`、`convertible_revision` | 可转债强赎、下修 | 高／中 |
| `regulatory_penalty`、`regulatory_inquiry` | 行政处罚、问询函 | 高／中 |
| `audit_opinion`、`auditor_change` | 非标审计、变更会计师 | 高／中 |
| `earnings_guidance`、`repurchase`、`shareholding_change` | 业绩预告、回购、增减持 | 中 |
| `litigation` | 诉讼、仲裁 | 高 |
| `annual_report`、`semiannual_report`、`quarterly_report` | 年报、半年报、季报 | 中／低 |
| `convertible_other`、`material_event`、`equity_change`、`other` | 其他可转债、重大事项、股权变动、未命中 | 低／中 |

匹配按表中顺序，首次命中获类。以上前五行事件类型可通过 `fetch_selected(settings, rows)` 按需批量下载并提取 PDF 文本；其他类型也可由 `announce fetch` 显式提取。类别查询可用巨潮的官方分类代码；“回购”等细分类仍以标题规则识别。`recent_for(settings, codes, since)` 只返回本地已同步、重要性为高或中的公告。

PIT：`published_at` 是供应商记录的公告时间，不等于交易日。盘后、休市日的公告须从**下一可交易时点**才可用于决策；盘中公告也须核对实际可见时间及策略成交时点。历史接口回查可能包含后续更正、撤回或补发；snapshot 的 `fetched_at` 仅记录本次观察时间，不能据今天回补的结果声称过去某时点已经可见。最近四日重查会覆盖同名日表，旧 snapshot 哈希保留但旧文件字节不保留，因此不能还原历史首见版本。首版不会自动生成 PIT 事件特征。

限制：巨潮接口每页实际最多 30 条，超过第 100 页会回到首页；单日超过 3,000 条时按深主板、创业板、沪主板、科创板、北交所五个互斥板块重查并校验条数。单个板块若仍超过 3,000 条则报错，不写入不完整分区。接口仍可能限流或更改字段；同步串行并限速，失败日期可重跑。标题分类存在误报和漏报。公开可获取不表示可再分发，PDF 和摘录的使用应遵守巨潮条款与原公告版权；不要公开镜像原文。
