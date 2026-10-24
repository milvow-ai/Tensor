# R7 — Outreach mailboxes and domains

**Date:** 2026-10-25  
**Source:** Gemini job 662c3769-6059-4e2e-b6fd-e9f766da3bb1 + checks of official public pages (2026-10-24).

## Q1 — Mailbox providers

| Item | Condensed finding | Source URL | Status |
|---|---|---|---|
| Google Workspace Business Starter | $7/user/mo annual commitment; trial 14 days. Flexible-plan $8.40 from Gemini; not confirmed on current official page. | [Pricing](https://workspace.google.com/pricing.html); [trial](https://support.google.com/a/answer/6388497) | Annual checked; flexible unverified |
| Google sending | Paid limits in Gemini: 2,000 messages/day, 10,000 recipients total and 3,000 external; trial 500/rolling 24h. SMTP/IMAP app passwords require 2-step verification; Gmail API uses OAuth. | [Limits](https://support.google.com/a/answer/166852); [App passwords](https://support.google.com/accounts/answer/185833); [Gmail API](https://developers.google.com/gmail/api/guides/sending) | Gemini report; not rechecked here |
| Microsoft 365 Business Basic | $7/user/mo paid yearly, confirmed; 30-day trial per Gemini. Monthly $8.40 reported by Gemini, not confirmed on checked official pricing page. | [Pricing](https://www.microsoft.com/en-us/microsoft-365/business/microsoft-365-plans-and-pricing) | Annual checked; monthly unverified |
| Microsoft sending and access | Gemini reports 10,000 recipients/day, 30 messages/min and 500 recipients/message; trial tenant limit 5,000 external/day. SMTP AUTH/IMAP OAuth and Graph available; Basic-auth retirement timing reported as Dec 2026 default disable, 2H 2027 removal. | [Exchange limits](https://learn.microsoft.com/en-us/office365/servicedescriptions/exchange-online-service-description/exchange-online-limits); [Basic auth update](https://techcommunity.microsoft.com/t5/exchange-team-blog/updated-exchange-online-smtp-auth-basic-authentication/ba-p/4090123); [deprecation](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-basic-authentication-exchange-online) | Gemini report; not rechecked here |

## Q2 — Domains

| Item | Condensed finding | Source URL | Status |
|---|---|---|---|
| Cloudflare Registrar | At-cost; free WHOIS redaction. Gemini quotes .com at $10.46/year, but current official page checked did not expose the exact .com price. | [Registrar](https://www.cloudflare.com/domains/) | Price unverified; privacy checked |
| Namecheap | .com renewal $18.48/year; page currently shows $6.48 first-year offer for new customers with code, and table lists $11.28 standard registration. Free privacy for life. | [Domain prices](https://www.namecheap.com/domains/) | Checked; ICANN $0.20 may be added |
| Root redirect | Cloudflare Single Redirects and Namecheap URL Redirect Record can redirect a domain root to the main site. | [Cloudflare](https://developers.cloudflare.com/rules/url-forwarding/single-redirects/); [Namecheap](https://www.namecheap.com/support/knowledgebase/article.aspx/385/2237/how-to-redirect-a-url-for-a-domain/) | Gemini report; not rechecked here |

## Q3 — Warm-up and sending tools

| Option | Condensed finding | Source URL | Status |
|---|---|---|---|
| Instantly | Current page shows Growth $47/mo, unlimited email accounts and warm-up; feature comparison lists API/webhooks/integrations. Gemini's claim that API is available on every paid plan not independently checked. | [Pricing](https://instantly.ai/pricing) | Growth checked; API tier detail unverified |
| Smartlead | Gemini reports Base $39/mo; Pro $94 monthly/$79 annual with API; Base lacks API. | [Pricing](https://smartlead.ai/pricing) | Gemini report; not rechecked |
| Stand-alone tools | Gemini reports lemwarm $29/mailbox monthly; Mailreach currently displays $19.50/mailbox/mo (Gemini had $25); Warmy $49/mailbox/mo; Warmbox Solo $19/mo. | [lemwarm](https://www.lemwarm.com/pricing); [Mailreach](https://www.mailreach.co/pricing); [Warmy](https://www.warmy.io/pricing); [Warmbox](https://www.warmbox.ai/pricing) | Mailreach checked; others Gemini only |
| Manual ramp | Gemini: Google advises gradual volume, avoid bursts and reduce if bounces rise; Microsoft warns spikes can affect outbound pool/reputation. | [Google](https://support.google.com/mail/answer/81126); [Microsoft](https://learn.microsoft.com/en-us/defender-office-365/outbound-spam-protection-about) | Gemini report; not rechecked |

## Q4 — Sender requirements at the planned volume

| Item | Condensed finding | Source URL | Status |
|---|---|---|---|
| Google | All senders: SPF or DKIM, valid forward/reverse DNS, TLS; keep spam rate below 0.10% and never reach 0.30%. Bulk threshold 5,000+/day adds SPF+DKIM, DMARC alignment and RFC 8058 one-click unsubscribe. | [Google sender guidelines](https://support.google.com/mail/answer/81126) | Gemini report; not rechecked |
| Yahoo | Gemini reports SPF or DKIM, forward/reverse DNS, complaint rate below 0.30%; bulk threshold 5,000+/day adds DMARC alignment and one-click unsubscribe. | [Yahoo best practices](https://senders.yahooinc.com/best-practices/) | Gemini report; not rechecked |
| Outlook.com | Gemini reports high-volume sender requirements: SPF, DKIM, DMARC alignment and PTR; unauthenticated bulk may be junked/rejected. | [Microsoft sender support](https://sendersupport.olc.protection.outlook.com/) | Gemini report; not rechecked |
| Applicability | Owner's stated ramp is ~25/day total (brief says ~100/day total); either is below the cited 5,000/day bulk threshold. Configure SPF, DKIM, DMARC and TLS from the outset; PTR is normally controlled by the sending-IP provider. CAN-SPAM applies regardless of volume: identify ads, include valid postal address and opt-out, honor opt-outs within 10 business days. | [FTC CAN-SPAM guide](https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business) | Threshold from Gemini; CAN-SPAM source linked |

## Q5 — Monitoring

| Item | Condensed finding | Source URL | Status |
|---|---|---|---|
| Google Postmaster Tools | Gemini reports current v2; low volume may show no data and Google does not publish an exact cutoff. | [Postmaster Tools](https://support.google.com/mail/answer/9981691) | Gemini report; not rechecked |
| Microsoft SNDS | Gemini reports it requires dedicated IP/CIDR visibility; shared mailbox-provider IPs are not useful for individual domain monitoring. | [SNDS FAQ](https://sendersupport.olc.protection.outlook.com/snds/faq.aspx) | Gemini report; not rechecked |
| Free checks | Gemini lists MXToolbox blacklist lookup and Spamhaus lookup. | [MXToolbox](https://mxtoolbox.com/blacklists.aspx); [Spamhaus](https://check.spamhaus.org/) | Gemini report; not rechecked |

## Load-bearing prices

| Provider | Item | Price/month or /year | Source URL | Status |
|---|---|---:|---|---|
| Google Workspace | Business Starter, annual | $7/user/month | [Official pricing](https://workspace.google.com/pricing.html) | verified |
| Namecheap | .com first-year promotional registration | $6.48/domain/year (code; new customers) | [Official domain prices](https://www.namecheap.com/domains/) | verified |
| Namecheap | .com renewal | $18.48/domain/year | [Official domain prices](https://www.namecheap.com/domains/) | verified |
| Cloudflare Registrar | .com registration/renewal | $10.46/domain/year per Gemini answer; exact current price not displayed on checked page | [Official registrar page](https://www.cloudflare.com/domains/) | unverified |
| Mailreach | Warm-up | $19.50/mailbox/month on current page | [Official pricing](https://www.mailreach.co/pricing) | verified |
| Google Workspace | Flexible monthly plan | $8.40/user/month per Gemini answer; not confirmed on current page | [Official pricing](https://workspace.google.com/pricing.html) | unverified |

## Priced proposal (owner decides; nothing bought)

Use two Namecheap .com domains and four Google Workspace Business Starter seats (two mailboxes per domain), annual commitment, then ramp gradually to about 25 total sends/day. Run a manual ramp and read replies through the owner's OAuth/API or supported mail access. Set SPF, DKIM, DMARC, TLS, a monitored opt-out process, and CAN-SPAM compliant footer before sending. No paid warm-up service is needed for this least-cost proposal; warm-up networks do not guarantee inbox placement.

- **Setup:** $12.96 for two promotional domain registrations (plus any applicable $0.20/domain ICANN fees); mailbox setup $0 stated by provider.
- **Monthly recurring, annualized:** $28 for four seats + $3.08 for two .com renewals ($18.48 × 2 ÷ 12) = **$31.08/month** before tax. Initial first-year domain spend annualizes to $1.08/month, so first-year annualized run rate is **$29.08/month**. Both are below $150/month.
- Price excludes any taxes and domain availability/premium-name differences. Namecheap's first-year promo is conditional; budget renewal at the regular renewal price.
