# r3 — Email layer (Q3)

> Seen 2026-10-03. Untagged vendor facts come from the primary-domain page, read only through a WebSearch summary (WebFetch egress-blocked, Firecrawl out of credits), so wording is not verbatim. GitHub and PyPI data read directly. [B] = competitor or third-party blog. UNV = unverified. * = host mailbox terms still bind. Free-tier unit = emails sent; resets UNV unless stated. Prices per user/month, annual billing.

## D rows

| Capability | Existing resource | Open source | Free option | Paid upgrade | Build ourselves? | Recommendation |
|---|---|---|---|---|---|---|
|Send (SMTP/API)|Zoho on milvow.com [00-audit]|warmbly (see E rows)|No ToS-clean $0 path: Zoho Free lacks IMAP/SMTP; SES and six ESPs bar unconsented lists|Workspace/M365 $7; Zoho Lite ₹59 (policy risk)|Adapters only|Buy 1–2 Workspace/M365 mailboxes on a separate domain|
|Mailbox-provider rules|DMARC p=none|—|Google: spam rate under 0.10%, never 0.30%; bulk about 5,000+/day; enforcement ramped Nov 2025 https://support.google.com/mail/answer/14229414. Yahoo: complaints under 0.3%; bulk adds DMARC https://senders.yahooinc.com/best-practices/. Outlook.com: 5,000+/day need SPF, DKIM, DMARC; 550 5.7.515 reject from 2025-05-05 https://techcommunity.microsoft.com/blog/microsoftdefenderforoffice365blog/strengthening-email-ecosystem-outlook%E2%80%99s-new-requirements-for-high%E2%80%90volume-senders/4399730|—|Counters, alerts|Tens/day is below bulk lines, but authenticate and align anyway|
|Separate domain, volume|None|—|Separate domain: Apollo https://knowledge.apollo.io/hc/en-us/articles/4409225311885 and vendors advise; no independent trial. Per inbox: Instantly up to 30/day, Smartlead 20–50, conflicting [B]|—|Config|Outreach domain, 10–30/day per mailbox, ramped [mine]|
|Reply ingestion|None|imap-tools 1.15.0 (2026-08-06, Apache-2.0) https://pypi.org/project/imap-tools/; mail-parser-reply 1.36 (2025-12-01, MIT) https://pypi.org/project/mail-parser-reply/; stale: talon (2017), email-reply-parser (2020)|IMAP IDLE (RFC 2177)|Gmail watch, Graph webhooks need public HTTPS|Thin threading|IDLE plus polling|
|Bounce/DSN|None|flufl.bounce 5.0.1 (2026-05-22, Apache-2.0) https://pypi.org/project/flufl.bounce/; avoid flanker (2019)|RFC 3464 parse https://datatracker.ietf.org/doc/html/rfc3464; SMTP 5xx at send|—|Hard/soft mapping|Use flufl.bounce|
|Complaints|None|—|Yahoo CFL (DKIM d= domain) https://senders.yahooinc.com/complaint-feedback-loop/; Postmaster hides low volume https://support.google.com/mail/answer/14668346; SNDS/JMRP need own IPs|—|Own counters|Treat "stop"/spam replies as suppress|
|Suppress/unsubscribe|None|—|mailto: List-Unsubscribe|HTTPS one-click, RFC 8058: DKIM-signed headers, one HTTPS URI, "List-Unsubscribe=One-Click" https://datatracker.ietf.org/doc/html/rfc8058; Google requires it for bulk marketing/promotional mail; Yahoo for bulk senders|Build, hashed list|mailto: now; one-click needs public URL|
|Warm-up, tracking|None|warmbly warm-up (UNV)|None|Vendor tools [B]|Skip|No independent evidence either way; pixels off [mine]|
|Sequencing, legal basis|None|harvey https://github.com/ethanplusai/harvey (MIT, 97 stars)|Tensor scheduler|Instantly/Smartlead $39–97 [B]|Build|Build; CASL s.13 puts proof on us: store URL, date, role fit|

## E rows

| Name | URL | Capability | Pricing (paid) | Free tier (exact limit, unit, what consumes it, resets?) | Trial | API (which plan) | Cold outreach permitted by AUP? | Key limitations | License | Local/VPS fit | Integration method | Maturity evidence | Relevance H/M/L |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
|Zoho Mail Forever Free|https://www.zoho.com/en-in/mail/zohomail-pricing.html|Mailbox (existing)|—|≤5 users, 5GB each; no IMAP/POP/ActiveSync for new accounts; external send 50–500/hour rolling, 100 recipients/msg https://www.zoho.com/mail/help/adminconsole/rates-and-limits.html|—|Zoho Mail API; plan UNV|Unlikely: policy demands "express and provable permission" https://www.zoho.com/policy.html|Web-only; suspension would hit milvow.com mail|Proprietary|SaaS|None usable|Incumbent|M|
|Zoho Mail Lite|https://www.zoho.com/en-in/mail/zohomail-pricing.html|Mailbox, SMTP/IMAP|₹59 (5GB), ₹75 (10GB); Premium ₹199|—|UNV|POST /api/accounts/{id}/messages, scope ZohoMail.messages.CREATE, host mail.zoho.in https://www.zoho.com/mail/help/api/post-send-an-email.html|Unlikely: "does not recommend" bulk or marketing mail|Dynamic limits; same policy risk|Proprietary|SaaS|SMTP 465/587, IMAP 993, API|Incumbent|M|
|Google Workspace Business Starter|https://workspace.google.com/pricing|Mailbox, Gmail API|$7; $8.40 flexible https://knowledge.workspace.google.com/admin/billing/compare-flexible-and-annual-fixed-term-payment-plans|—|UNV|Gmail API via OAuth, plan UNV; watch needs Pub/Sub, renew within 7 days https://developers.google.com/workspace/gmail/api/guides/push|UNV|Unique recipients/day 3,000 (2,000 external) https://knowledge.workspace.google.com/admin/gmail/gmail-sending-limits-in-google-workspace; OAuth required since 2025-05-01 (app password for devices)|Proprietary|SaaS|Gmail API or OAuth SMTP/IMAP|Sets bulk rules|H|
|Microsoft 365 Business Basic|https://www.microsoft.com/en-us/microsoft-365/business/microsoft-365-business-basic|Mailbox, Graph|$7.00; $8.40 monthly|—|UNV|Graph sendMail (OAuth); Outlook subscriptions expire within 10,080 min https://learn.microsoft.com/en-us/graph/outlook-change-notifications-overview|UNV|10,000 recipients/day, 30 messages/min https://learn.microsoft.com/en-us/office365/servicedescriptions/exchange-online-service-description/exchange-online-limits; SMTP AUTH Basic off by default end-Dec 2026 https://techcommunity.microsoft.com/blog/exchange/updated-exchange-online-smtp-auth-basic-authentication-deprecation-timeline/4489835|Proprietary|SaaS|Graph or OAuth SMTP|Sets Outlook.com rules|H|
|Amazon SES|https://aws.amazon.com/ses/pricing/|Bulk/transactional send|$0.10 per 1,000 emails; new accounts on Essentials plan from 2026-07-21 https://aws.amazon.com/about-aws/whats-new/2026/07/amazon-ses-pricing-plans/|SES tier (3,000/month) closed to new customers 2026-07-21; AWS credits up to $200, 6-month free plan|Credits|SES API/SMTP; plan UNV|No: AWS AUP bars "unsolicited mass email" https://aws.amazon.com/aup/; unsolicited = "emails that the recipient didn't explicitly ask to receive" https://docs.aws.amazon.com/ses/latest/dg/faqs-enforcement.html|Suspension risk|Proprietary|SaaS|SMTP/API|Mature, UNV|L|
|Resend|https://resend.com/pricing|Transactional API|UNV|100/day, 3,000/month https://resend.com/docs/knowledge-base/account-quotas-and-limits|—|UNV|No: bars unsolicited mail incl. cold outreach, purchased or scraped lists https://resend.com/legal/acceptable-use|Transactional only|Proprietary|SaaS|REST/SMTP|—|L|
|Postmark|https://postmarkapp.com/pricing|Transactional|UNV|100/month (Developer)|—|UNV|No: permission-based lists only; purchased or rented prohibited https://postmarkapp.com/terms-of-service/|Transactional focus|Proprietary|SaaS|REST/SMTP|—|L|
|Brevo|https://www.brevo.com/pricing/|Marketing, transactional|UNV|300/day|—|UNV|No: scraped or purchased lists prohibited https://www.brevo.com/legal/antispampolicy/|Approval before sending|Proprietary|SaaS|REST/SMTP|—|L|
|Mailgun|https://www.mailgun.com/pricing/|Transactional|UNV|100 messages/day https://help.mailgun.com/hc/en-us/articles/203068914-What-does-the-Free-plan-offer|—|UNV|No: bought, rented or scraped lists prohibited https://www.mailgun.com/legal/aup/|—|Proprietary|SaaS|REST/SMTP|—|L|
|SendGrid (Twilio)|https://www.twilio.com/en-us/products/email-api/pricing|Transactional|UNV|100/day for 60 days https://support.sendgrid.com/hc/en-us/articles/35270136965403|60 days|UNV|No: bars addresses "obtained from the Internet or social media without prior affirmative consent" https://support.sendgrid.com/hc/en-us/articles/4404316003483|No permanent free plan|Proprietary|SaaS|REST/SMTP|—|L|
|MailerSend|https://www.mailersend.com/pricing|Transactional|UNV|500/month, 100/day https://www.mailersend.com/help/plans-features-and-limits|—|UNV|No: third-party-acquired addresses barred https://www.mailersend.com/legal/terms-of-use|—|Proprietary|SaaS|REST/SMTP|—|L|
|Instantly|https://instantly.ai/pricing|Cold sequencer, warm-up|Growth $47, Hypergrowth $97/month [B] https://instantly.ai/blog/instantly-vs-smartlead-lemlist-2026/|UNV|UNV|Growth and above [B]|Yes* (UNV)|Unlimited accounts on flat fee [B]|Proprietary|SaaS|Mailbox connect UNV, REST|Annual benchmark report|M|
|Smartlead|https://www.smartlead.ai/|Cold sequencer|Base $39, Pro $94/month [B]|UNV|UNV|Pro [B]|Yes* (UNV)|—|Proprietary|SaaS|Mailbox connect UNV, REST|—|M|
|lemlist|https://www.lemlist.com/|Multichannel sequencer|Email Pro $55–69, Multichannel $79–99; extra inbox $9 [B]|UNV|14 days, no card [B]|UNV|Yes* (UNV)|Per-seat pricing|Proprietary|SaaS|Mailbox connect UNV|—|L|
|Saleshandy|https://www.saleshandy.com/|Cold sequencer|Starter $36, Pro $99, Scale $199 [B]|UNV|UNV|UNV|Yes* (UNV)|Unlimited accounts [B]|Proprietary|SaaS|Mailbox connect UNV|—|L|
|Woodpecker|https://woodpecker.co/|Cold sequencer|From $35 per 500 prospects [B]|UNV|Free trial [B]|UNV|Yes* (UNV)|Priced per prospect|Proprietary|SaaS|Mailbox connect UNV|—|L|
|Reply.io|https://reply.io/|Multichannel sequencer|Email Volume $49, Multichannel $89, Agency $166 [B]|None [B]|Yes [B]|UNV|Yes* (UNV)|—|Proprietary|SaaS|Mailbox connect UNV|—|L|
|Apollo sequences|https://www.apollo.io/|Data plus sequences|From $49 per user [B]|10,000 email credits/month [B]; sequence limits UNV|14 days [B]|UNV|Yes* (UNV)|Data side in r1|Proprietary|SaaS|Mailbox connect UNV|—|M|
|Hunter campaigns|https://hunter.io/|Finder plus campaigns|Starter $49, Growth $149, Scale $299 [B]|UNV|UNV|UNV|Yes* (UNV)|—|Proprietary|SaaS|Mailbox connect UNV|—|L|
|warmbly|https://github.com/warmbly/warmbly|Open-source outreach, warm-up|Free|Self-hosted|—|UNV|Own infra*|Young project|Apache-2.0|PC/VPS UNV|Self-host|348 stars, created 2026-01-17, pushed 2026-10-03|M|
|harvey|https://github.com/ethanplusai/harvey|Claude-Code sales agent|Free|Self-hosted|—|UNV|Own infra*|Young project|MIT|PC UNV|Self-host|97 stars, created 2026-03-12, pushed 2026-09-13|L|
|imap-tools|https://pypi.org/project/imap-tools/|IMAP client (IDLE support UNV)|Free|—|—|—|—|—|Apache-2.0|In-process Python|IMAP library|1.15.0 released 2026-08-06|H|
|flufl.bounce|https://pypi.org/project/flufl.bounce/|Bounce/DSN detectors|Free|—|—|—|—|—|Apache-2.0|In-process Python|Library|5.0.1 released 2026-05-22|H|
|mail-parser-reply|https://pypi.org/project/mail-parser-reply/|Reply-text extraction|Free|—|—|—|—|—|MIT|In-process Python|Library|1.36 released 2025-12-01|M|
|mail-parser|https://pypi.org/project/mail-parser/|MIME/DSN parsing|Free|—|—|—|—|—|Apache-2.0|In-process Python|Library|4.8.0 released 2026-10-01|M|
|IMAPClient|https://pypi.org/project/IMAPClient/|IMAP client|Free|—|—|—|—|—|UNV (blank on PyPI)|In-process Python|Library|4.1.0 released 2026-09-18|M|
|flanker, talon, email-reply-parser|https://pypi.org/project/flanker/|Parsing (stale)|Free|—|—|—|—|Unmaintained|Apache-2.0, Apache-2.0, MIT|In-process Python|Library|Last releases 2019-12-05, 2017-08-24, 2020-10-07|L|
|aioimaplib|https://pypi.org/project/aioimaplib/|Async IMAP client|Free|—|—|—|—|GPL-3.0 limits reuse|GPL-3.0|In-process Python|Library|2.0.1 released 2025-01-16|L|

## Compliance rules: US vs Canada

| Rule | US (CAN-SPAM) | Canada (CASL) | Official citation |
|---|---|---|---|
|Consent|Opt-out; no prior consent|Opt-in: express or implied consent first|FTC guide https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business; Act https://laws-lois.justice.gc.ca/eng/acts/e-1.6/fulltext.html|
|Published address|n/a|s.10(9)(b): implied if recipient "has conspicuously published" the address, publication is "not accompanied by a statement that the person does not wish to receive unsolicited commercial electronic messages" and message "is relevant to the person's business, role, functions or duties in a business or official capacity"|Act; CRTC guidance https://crtc.gc.ca/eng/com500/guide.htm|
|B2B exemption|n/a|Only if organisations "have a relationship" and message "concerns the activities of the organization to which the message is sent"; cold prospects fail|CRTC FAQ https://crtc.gc.ca/eng/com500/faq500.htm|
|Identification, unsubscribe|Accurate From/To/Reply-To; valid postal address; ad flagged "clear and conspicuous"; "functioning return email address or similar Internet-based mechanism"; 10 business days UNV|Business name, on-behalf-of name, mailing address plus phone/email/web, valid 60 days; stop within 10 business days; link works 60 days|FTC guide; https://ised-isde.canada.ca/site/canada-anti-spam-legislation/en/getting-consent-send-email|
|Proof, records|No consent-record duty seen (UNV)|s.13: whoever alleges consent "has the onus of proving it"; keep consent and unsubscribe logs|Act; https://www.canada.ca/en/radio-television-telecommunications/news/2016/07/enforcement-advisory-notice-for-businesses-and-individuals-on-how-to-keep-records-of-consent.html|
|Penalties|Up to $53,088 per email (FTC page)|AMP up to $1,000,000 individual, $10,000,000 other, per violation (s.20(4)); private right of action suspended 2017|FTC guide; Act; https://www.canada.ca/en/innovation-science-economic-development/news/2017/06/government_of_canadasuspendslawsuitprovisioninanti-spamlegislati.html|
|Sent from outside|Scope UNV|Yes: spam "sent into Canada ... is subject to CASL no matter what country it comes from"; foreign-state exemption concerns mail accessed abroad [mine]|https://ised-isde.canada.ca/site/canada-anti-spam-legislation/en/understand-canadas-anti-spam-legislation/understand-canadas-anti-spam-legislation-sub/understanding-canadas-anti-spam-legislation|

## Benchmarks

| Claim | Producer, sample, method | Conflict of interest | Grade |
|---|---|---|---|
|Reply 3.43% avg; top 10% over 10%; follow-ups 42% of replies|Instantly https://instantly.ai/cold-email-benchmark-report-2026; "billions" of emails, 2025-01-01 to 12-18; reply definition UNV|Sells cold-email platform|C|
|Reply 0.45% (unique replies ÷ sent); step 1 0.59%; follow-ups 58.6% of replies; step 3+ gives over 53% of meetings|Belkins https://belkins.io/blog/cold-email-response-rates; 7,530,489 emails, its own 2025 client campaigns|Sells outbound services|B-|
|Sequence reply 4.5%|Hunter https://hunter.io/the-state-of-cold-email/; 31M emails by its users, 2025; method UNV|Sells finder and campaigns|C|
|Top reps 4.2x replies, 8.1x meetings (relative only)|Gong Labs with 30MPC https://www.gong.io/blog/does-cold-email-even-work-any-more-heres-what-the-data-says; 85M+ emails, 2025|Gong sells revenue AI; 30MPC sells training|C|
|Open tracking off lifted replies 1.08% to 2.36%|Snov.io https://snov.io/blog/cold-email-statistics/; 44M emails (title says 10M+)|Sells outreach tool|D|
|Woodpecker, lemlist figures; any positive-reply rate|Not captured|—|UNV|

Grades: B stated n and method; C vendor, partial method; D claim only. Instantly and Belkins differ 7x and disagree on follow-up share: plan on roughly 0.5–3% replies [mine].

## Architecture implications

1. Keep the five-method port; adapters speak mailbox protocols (SMTP/IMAP, Gmail API, Graph). No ESP adapter for cold mail.
2. Model each mailbox as a ledger connection (Zoho 50–500/hour, Workspace 2,000 unique external recipients/day, Exchange 10,000/day and 30/minute). Tensor's tens/day sits far below, so reputation binds, not quota.
3. Gate send() on a CASL basis record (URL where published, date, no no-spam notice, role relevance), kept while we email; refuse otherwise. Build in the identification block and a 10-business-day unsubscribe SLA. mailto: List-Unsubscribe always; HTTPS one-click only with a public endpoint, which a PC lacks.
4. Inbound: IMAP IDLE with polling fallback, no public URL. DSNs via flufl.bounce plus SMTP 5xx at send. Gmail watch and Graph subscriptions lapse within 7 days; renew.
5. Postmaster Tools hides low volume and SNDS/JMRP need own IPs, so Tensor's own bounce and complaint counters are the signal; auto-pause on thresholds [mine].
6. Pixels off by default [mine]. M365 needs OAuth after Dec 2026.

## Unverified / blocked

- Tools: WebFetch egress-blocked (zoho.com, instantly.ai, support.google.com); Firecrawl returned 402; gh search blocked. All untagged vendor facts are search summaries: re-read CASL s.10(9)(b), Zoho policy and Google/Yahoo/Microsoft pages before relying.
- Not confirmed: Zoho API plan gating, .in hostnames, monthly price; Workspace 2,000 messages/day, Gmail API quotas; Workspace/M365 spam clauses; FTC 10-business-day wording, current penalty, extraterritorial scope; RFC 3463/6522; IDLE re-issue interval; Graph send limits; Postmark "25,000 credits" FAQ; SES sandbox; Mautic, listmonk, Postal.
- All cold-platform prices, API tiers, trials and mailbox-connect methods are [B]. Per-inbox volume is vendor-only and conflicting; one summary credited Google with 30–50/day, unconfirmed, unused.
- Warm-up evidence is vendor-only (Saleshandy: 72–76% to 90%+ inbox placement in 4 weeks); open-tracking evidence is Snov.io and lemlist (Apple Mail "64.66%" fake opens), both sellers. No independent study found for either, nor for separate-domain advice.
