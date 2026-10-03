# R2 — Market evidence for vertical selection (Q2)

Written 2026-10-03. **E#** = sourced fact (producer, date). **[A]** = assumption. **[J]** = my judgement, not a published framework.

**Search log.** WebSearch and Firecrawl; market facts preferred from the last 6 months, older items dated in the table. Opened as pages: E1, E2–E3 (first 30 of 79 pages), E4–E5, E7 (WebFetch summary). Every other row rests on search-index summaries. WebFetch to census.gov, api.census.gov, bls.gov, statcan.gc.ca, hiringlab.indeed.com and upwork.com was egress-blocked; Firecrawl credits ran out after three scrapes.

**GRADE** rates the figure as measured. No source measures agency purchases directly, so every row is indirect for Q2.

**Scoring:** weighted sum V = Σ wᵢvᵢ (Keeney & Raiffa, 1976), each vᵢ min–max scaled across candidates.

## Evidence table

| Source | Publisher, date | Finding | GRADE | Conflict of interest |
|---|---|---|---|---|
| E1 BTOS AI-use story | US Census Bureau, 26 May 2026 (data to 3 May) | 19.8% of firms use AI in any business function; Information 39.7%, Finance/Insurance 33.9%, Retail about 14%. 37% of firms with 250+ staff, under 20% with 1–4. Wording changed Nov 2025: no trend across it. | High | None; self-reported |
| E2 BTOS AI supplement, CES-WP-26-25 | Census Bureau CES working paper, 2026 (Nov 2025–Jan 2026) | Firm-weighted AI use now / next 6 months for 20 sectors, from agriculture 5/8 to information 38/43. Candidate sectors are in the Candidate table. | High | None |
| E3 Same paper, functions | Same | Among AI users: sales and marketing 52%, IT 42%, customer service 28% (42% expected), finance and accounting 24% (39%). Writing, document analysis, information search lead worker tasks. 66% only augment; AI-linked job cuts in 2% of firms. | High | None |
| E4 CSBC Q2 2026 | Statistics Canada, 11 Jun 2026 (9,251 responses) | 19.2% used AI in 12 months (6.1% in 2024). Professional-technical 32.4%, wholesale 7.9%, construction 9.2%. Chatbots 28.2%, LLMs 24.8%, RPA 5.0%. 40.0% say AI is not relevant. | High | None |
| E5 Same survey | Same | Among AI users, 30.2% of firms with 100+ staff used outside consultants or vendors; 10.7% of firms with 1–4 staff did. Barriers: privacy/cyber 13.4% (health care 26.4%), cost 10.6%. | High | None |
| E6 CSBC Q3 2026 (summary only) | Statistics Canada, 31 Aug 2026 | 25.2% plan AI within 12 months (14.5% a year earlier). Professional-technical 54.8%; construction 3.2% to 18.3% (2024 to 2026); firms with 20–99 staff 15.0% to 32.8%. | Moderate | None |
| E7 Economic Index | Anthropic, 15 Jan 2026 (data 13–20 Nov 2025) | 75% of first-party API use is automated; office and admin tasks 13% of traffic. Named tasks: B2B cold-email drafting 0.47%, business-reply drafting 0.28%, invoice-processing systems 0.24%. No industry split seen. | Low | Anthropic sells Claude; Tensor uses it |
| E8 AI postings series | Indeed Hiring Lab, Jul 2026 (summaries) | AI-related postings 5.9% in June 2026 (3.3% peak, 2022); about 45% of data-analytics, 15% of marketing, 9% of HR postings. Hiring, not purchasing; no NAICS series. | Moderate | Indeed sells job ads |
| E9 AI report; website study | Clutch, 18 Aug 2026 (n=600 AI-using US small firms); Aug 2025 | 62% plan AI agents, 46% AI–software integration, 42% data analysis. Agencies built 45% of sites covered; 81% had redesigned at least once. | Low | Sells listings and leads to agencies; sample conditioned on AI use |
| E10 AI within SMBs | Upwork, 2026 (n=750 leaders) | 62% plan to hire freelance AI specialists within 3 months; 34% pilot workflow-automation agents; most productivity gains under 25%. | Low | Sells freelance labour |
| E11 SMB Trends; State of Sales 2026 | Salesforce (date unverified; n=3,350); HubSpot (1,000+ sales leaders) | 75% of SMBs invest in AI. 65% of salespeople lose a business day a month reconciling data across systems. | Low | Both sell CRM and AI agents |
| E12 Agent surveys, forecasts | Zapier, late 2025; McKinsey, Aug 2026; Gartner, Jun 2025 and May 2026 (recaps) | 72% of enterprises use or test agents. Scaling agents: 40% of $1B+ firms, smaller firms flat at 22%. Over 40% of agentic projects forecast cancelled by end-2027. Enterprise-skewed. | Low | Sell automation, AI consulting, advisory |
| E13 WebAIM Million 2026 | WebAIM, Utah State Univ., 2026 (recaps) | 95.9% of top-million home pages fail automated WCAG 2 checks (94.8% in 2025). Not SMB-specific; too common to separate verticals. | Moderate | Minor: sells accessibility training |
| E14 Cold-email benchmarks; saturation posts | Instantly, Woodpecker, 2026 (aggregators); blogs, forums | Mean reply 3.1–3.4%. Woodpecker by recipient industry: consulting 4.8%, IT 3.9%, finance 2.8%, manufacturing 2.4%, health care 1.9%. Blogs call generic "AI automation agency" pitches saturated and niche pitches open; no vertical data. | Low; blogs Very low | Tool vendors; agency-selling bloggers |

**Read-across by topic [J].** Support and chat: strongest official signal (customer-service use 28% to 42% expected, E3; chatbots 28.2% of Canadian AI users, E4). Document and data work: writing, document analysis and finance lead (E3, E7, E12). Sales and CRM: top function (52%, E3), plus vendor-reported reconciliation pain (E11). Websites: only Low-grade purchase evidence (E9). Workflow automation and scheduling: RPA is 5.0% (E4); no official purchase data. Agents: pilots common, scaling among smaller firms flat (E10, E12).

## Selection criteria → dataset that measures each

Weights are **[A]**: equal, 0.10 each, until the founder sets them; test rank stability at ±50%.

| Criterion | Dataset (producer) | Status |
|---|---|---|
| Observable need | Postings for admin, operations and support roles (Indeed Hiring Lab); OEWS office-admin share; per-prospect crawl | Indeed by occupation only (E8) |
| Ability to pay | SUSB receipts and payroll per firm by NAICS and size; CBP payroll per establishment (Census) | Not retrieved |
| Decision-maker accessibility | SUSB/CBP size mix (owner-led share); StatCan Canadian Business Counts; provider coverage (r1) | Not retrieved |
| Digital maturity | BTOS AI use by sector (E2); CSBC (E4, E6) | Sector level only |
| Repetitive workflows | BLS OEWS office-admin (43) and sales (41) share by NAICS-4; BTOS functions (E3) | OEWS not retrieved |
| Website opportunity | Sampled audits per vertical (Tensor web.audit, r5); HTTP Archive; WebAIM (E13) | Vertical: unknown |
| Agency purchasing behaviour | CSBC outside-help use (E5, by size only); Clutch, Upwork (Low) | Industry: unknown |
| Competitive intensity | No public dataset; proxies: Clutch category counts, ad libraries, own reply rates | Unknown |
| Public researchability | Licence registries (bar, CPA, insurance), website presence, provider coverage (r1) | Not retrieved |
| Fit for personalised outbound | Own campaign results; CAN-SPAM and CASL basis (r3); benchmarks (E14, Low) | Own data only |

## Candidate verticals

**[A]** The list is my selection of B2B-leaning, SMB-dominated industries, not a data output. Sector-level AI use stands in for NAICS-4 maturity. Facts are E-referenced; v4 is digital maturity (BTOS "now", E2) scaled over these ten; the last column is **[J]**.

| Candidate (NAICS 2022) | Facts | v4 | [J] Offer: reason |
|---|---|---|---|
| Legal services 5411 | Sector 54: 34/37. Canada professional-technical 32.4% now, 54.8% planned | 1.00 | B: document analysis and writing lead AI tasks (E3), matching intake and drafting. Privacy barrier unknown. |
| Accounting, bookkeeping, payroll 5412 | Sector 54, as above | 1.00 | B: finance and accounting use expected 24% to 39% (E3). |
| Architecture, engineering 5413 | Sector 54, as above | 1.00 | Both: no discriminating evidence; test. |
| Management, technical consulting 5416 | Sector 54; nearest reply benchmark 4.8% (E14) | 1.00 | A: positioning is their product; they may self-serve B. |
| Insurance agencies, brokerages 5242 | Sector 52: 29/34 (includes banks); Canada finance-insurance 40.4% now; reply benchmark 2.8% | 0.81 | B: service, quote intake and document handling. |
| Employment services 5613 | Sector 56: 17/19 | 0.37 | B, low confidence: candidate and client intake. |
| Machinery, equipment wholesale 4238 | Sector 42: 13/19; Canada wholesale 7.9% now | 0.22 | Both: low maturity, rising plans; capacity to implement unknown. |
| Fabricated metal manufacturing 332 | Sector 31–33: 12/17; Canada skilled-labour barrier 12.0%; reply benchmark 2.4% | 0.19 | Both, low confidence: quote-request site (A) and intake (B). |
| Freight arrangement 4885 | Sector 48–49: 7/10 | 0.00 | B: quote and dispatch email; receptivity unclear. |
| Building equipment contractors 2382 (includes HVAC) | Sector 23: 9/12; Canada construction 9.2% now; planned 3.2% to 18.3% | 0.07 | Both: a comparator, not a presumption. |

**Gaps.** Criteria 1–3 and 5–10 are unscored for every candidate: no OEWS shares, firm counts, NAICS-4 AI use, vertical website-quality or saturation data. Coverage is 1 of 10 criteria, so a total weighted score would be arithmetic on one column; it is withheld. Sector data cannot separate the four 54xx rows.

**Judgement [J].** The retrieved facts show the highest current and planned use in professional-technical and finance-insurance (E2, E4, E6), and low current but rising planned use in wholesale, manufacturing, construction and transport. Strongest alternative: start in high-use sectors, where receptivity is evidenced; it loses for now because those firms can also self-serve (unverified). Falsifier: equal-size pilots in a high-use and a low-use vertical show no difference in positive replies per 100.

## Implications for Tensor's market-research module

1. Store each vertical as a hypothesis row: criterion values, source id, GRADE, retrieval date and a missing flag. Compute the weighted sum over populated criteria only and always show coverage (Keeney & Raiffa, 1976).
2. Ingest official tables as versioned datasets with refresh dates: BTOS, CSBC, OEWS, SUSB/CBP, StatCan business counts. Sector data cannot separate NAICS-4 verticals, so differentiation must come from prospect-level observations (website audit, job posts).
3. Carry firm size in every hypothesis. Official data put outside-help use (E5) and planned adoption (E6) higher at 20+ staff; default ICP 20–249 staff **[J]**.
4. Store each source's conflict of interest. Keep Low and Very low statistics out of email claims.
5. Treat positive replies per 100 sent, by vertical and offer, as the only available measure of purchasing behaviour and saturation. Run equal-size pilots in three or four verticals, at least one low-maturity.
6. Name a workflow, not "AI": 40% of Canadian firms call AI not relevant (E4).
7. Check network access to bls.gov, census.gov and statcan.gc.ca before the next run.

## Unverified

- OEWS office-admin and sales shares by candidate NAICS: not retrieved; a search surfaced only a May 2023 top-industry list, not used.
- Firm counts by size (SUSB 2022, CBP, StatCan Canadian Business Counts): not retrieved. Next sources: census.gov/data/tables/2022/econ/susb/2022-susb-annual.html, census.gov/hfp/btos/data, and the bls.gov/oes/current/naics4_524200.htm URL pattern.
- E6, E8, E12, E13, E14 are search-summary reads; figures unchecked against primary pages. E11 date and E10 sample frame unknown.
- Anthropic March and June 2026 editions, Indeed sector series, BTOS NAICS-4 and function-by-sector tables: not read.
- Unknown: what firms buy from agencies by vertical; website quality by vertical; AI-agency outreach saturation by vertical.
- StatCan TechStat (from 2027) will publish new AI-use statistics.
