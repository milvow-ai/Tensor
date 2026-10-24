# T0-R7 — Outreach domains and mailboxes: verified facts and prices (research worker, 2026-10-24)

You are a research worker. Return markdown only (the lead saves it as `research/r7-outreach-mailboxes.md`).
Read public web pages only: no sign-ups, logins, purchases, form submissions or trials. Do not recommend; report facts,
figures and costs. Never answer from memory: every figure needs a page you fetched today, its URL and the exact figure.

## Context
Milvow (an AI-automation agency) will send US B2B cold email from new outreach domains, never from milvow.com. Pilot:
2 domains × 2 mailboxes, each ramping to about 25 sends a day (≈ 100 a day in total), with replies read by our own code.
Budget ≤ $150 a month for everything. Our code sends through SMTP/IMAP or a provider API and applies its own caps.

## Questions
1. **Mailbox providers.** Google Workspace Business Starter and Microsoft 365 Business Basic (or Exchange Online Plan 1):
   US price per user per month on the monthly/flexible plan and on the annual plan; free trial terms; published daily
   sending and recipient limits, including any lower limits for new or trial accounts; programmatic access for sending
   and reading (Google: SMTP/IMAP with App Passwords, Gmail API with OAuth; Microsoft: SMTP AUTH and the current Basic-auth
   retirement dates, IMAP, Microsoft Graph). Quote the official pages.
2. **Domains.** `.com` registration and renewal price at Cloudflare Registrar and at Namecheap; WHOIS privacy; whether the
   new domain's root can redirect to milvow.com.
3. **Warm-up and sending tools (options only).** (a) Platforms with built-in warm-up and an API (Instantly, Smartlead):
   plan prices, mailbox limits, which plan includes the API. (b) Stand-alone warm-up (lemwarm, Mailreach, Warmy, Warmbox):
   price per mailbox. (c) A manual ramp with no warm-up network: any guidance Google or Microsoft publish on new senders.
4. **Sender requirements at ≈ 100 a day.** Google's email sender guidelines (all senders vs bulk senders), Yahoo's sender
   requirements, and Microsoft Outlook.com's requirements for high-volume senders: SPF, DKIM, DMARC, alignment, one-click
   unsubscribe (RFC 8058), spam-rate thresholds, PTR/TLS. Quote the numbers and say which apply at ≈ 100 a day.
5. **Monitoring.** Google Postmaster Tools (current version, low-volume visibility), Microsoft SNDS, and free blocklist
   or reputation checks.

## Output (≤ 150 lines)
- Sections Q1–Q5. Each a table: Item | Figure | Source URL | Retrieved (2026-10-24) | Grade (High = official page,
  Moderate = official but indirect, Low = third party).
- "Cost at pilot" table, per month, for: (i) Google, 4 mailboxes + 2 domains + manual ramp; (ii) the same + a stand-alone
  warm-up tool; (iii) the same + a sending platform; (iv) Microsoft in place of Google for (i).
- "Could not verify": every figure you could not confirm on an official page today.
