# Pitch kit

Material for the 5-minute video and the application form. Every number in
here is real output of `python3 -m engine demo` (seed 42) — nothing is
projected, rounded up, or aspirational.

## One-liner

**Sanjeevani** — the revenue resuscitation engine. It finds the money that's
flatlining (failed payments, abandoned carts, paused mandates, ageing
invoices), diagnoses *why*, runs the one playbook that fits, and proves every
rupee and every refusal in a tamper-evident ledger. AI writes the words;
deterministic, audited code decides when money moves.

## What it solves (form field)

Merchants lose revenue in four quiet ways — payment degradation, checkout
drop-off, subscription failure, overdue B2B invoices — and today's response is
either a dumb retry cron or an intern with a spreadsheet. Sanjeevani closes
the loop end-to-end: detect -> root-cause -> bounded intervention -> measured
recovery, under hard compliance guardrails (RBI pre-debit notices, quiet
hours, consent, dispute freezes) and hard stopping rules (contact caps,
negative-expected-value stops, circuit breakers, a 14-day horizon). Output is
not a claim but evidence: a reproducible batch run recovering ~57% of at-risk
value, with a hash-chained audit trail you can verify — and break — yourself.

## How this stands apart (form field — "what makes your project different")

Against a field of this size, most submissions in this track will be one of
three things: an LLM chatbot that talks to debtors, a dunning cron with a
dashboard, or a deck with no runnable code. Sanjeevani's separation, point by
point, all verifiable in one command:

1. **Measured, reproducible money — with a baseline, not just a number.**
   Seed 42 recovers ₹1,00,36,502.35 of ₹1,75,33,179 at risk (57.2%) for
   ₹2,096.10 of spend, to the byte, on the reviewer's laptop (a test asserts
   the ledger head hash). And the repo ships the strawman: `python3 -m engine
   baseline` runs a real nightly dunning cron in the same world — 40.1%
   recovery, 2,525 contacts, 7,195 policy violations vs Sanjeevani's 57.2%,
   314 contacts, zero. The delta is exactly what the brain adds.
2. **The agent's refusals are the product.** It was told "no" **140 times**
   in one batch — quiet hours (66), consent (52), voice windows (11),
   small-ticket economics (6), weekly caps (4), negative expected value (1) —
   and every refusal is a row in the hash chain with the same ceremony as an
   action. Nobody else will demo their agent *declining* to act.
3. **Counter-metrics we volunteer.** Everyone reports money recovered; we
   also report the customer's side: hard caps of 4 contacts per case and 3
   per customer per week (enforced, not aspirational — batch-wide invariant
   tests), promises-to-pay honored 9 made / 9 kept / 0 broken, and disputed
   invoices frozen to a human within the hour. Recovery that burns trust is
   churn with extra steps.
4. **Tamper-evident by construction.** Edit one character of the 1,560-entry
   audit ledger and `python3 -m engine verify` names the exact entry.
   Compliance here is not a PDF; it's cryptography.
5. **AI in its right place.** The contrarian bet: determinism where money
   moves, Claude where words are written — with zero decision authority and
   a full offline fallback.
6. **Zero dependencies, ~2-second demo.** Pure Python stdlib. Nothing to
   install, no API keys needed, nothing that can fail on a judge's machine.

## 5-minute video script — technical walkthrough

Format: screen-share the whole way; you are driving a terminal and an
editor, not slides. Every beat has a **SHOW** (what's on screen — run these
exact commands) and a **SAY** (your spoken lines, timed to ~140 words/min).
Rehearse the tamper demo and the baseline run once so there's no dead air.
Pre-record setup: terminal font large, repo open in the editor, `out/`
freshly generated, Razorpay test keys exported (see checklist below).

**[0:00–0:25] Cold open: run it.**
SHOW: empty terminal → type `python3 -m engine demo` → output scrolls,
cursor lands on the recovery block.
SAY: "This is Sanjeevani, my entry for AI Revenue Recovery, and this is the
whole system running: two hundred at-risk cases, ₹1.75 crore in danger,
₹1 crore recovered — 57.2 percent — for ₹2,096 of channel spend. Two
seconds, pure Python standard library, zero dependencies, and seed 42 means
you'll get these exact rupees, byte for byte, on your machine. Let me show
you how it works."

**[0:25–0:55] The pipeline and the bet.**
SHOW: README module tree (or ARCHITECTURE.md diagram): datagen → diagnose →
triage → playbooks → guardrails → engine → world / ledger.
SAY: "The pipeline is detect, diagnose, triage, treat. One architectural bet
drives everything: every decision that touches money or a customer is
deterministic, tested, policy-as-code — because you cannot put a stochastic
model in charge of debiting bank accounts under RBI rules. Claude drafts
the Hinglish outreach copy — and that's all; zero decision authority, full
offline fallback. Now the interesting modules, in code."

**[0:55–1:35] Diagnosis picks the cure (the brain, in code).**
SHOW: `engine/diagnose.py` (signal → root-cause mapping), then
`engine/playbooks.py` scrolled to `PAYDAY_PATROL` — point at
`"salary_day"` — and the finite `Step` tuples.
SAY: "Cases arrive with observable signals only — gateway error codes,
funnel stages, invoice age. Diagnosis maps them to nine root causes, and
the root cause picks the playbook, because the right intervention depends
on *why* the money is stuck. Look at payday_patrol: an insufficient-funds
failure isn't retried when a cron fires — this declarative delay,
salary_day, retries when the customer's salary lands. An expired card never
gets a retry at all; it gets a new-card capture link. And every playbook is
a finite tuple — there is nothing after the last step, so a runaway loop is
structurally impossible."

**[1:35–2:15] Guardrails: the 13 no's (policy-as-code).**
SHOW: `engine/guardrails.py` — the `Verdict` dataclass, then scroll slowly
through rule 8 (quiet hours), rule 10 (RBI pre-debit window), rule 12
(negative expected value). Flash `engine/config.py`'s `Policy` dataclass.
SAY: "Before any step executes, it passes thirteen guardrails that return
proceed, defer, skip, or stop. Quiet hours defer to eight a.m. — deferred,
not dropped. Rule ten is the RBI e-mandate rule: no re-presentation without
a pre-debit notice at least 24 hours old — and the notice goes stale at 72,
so even my own playbook gets blocked if the cadence is wrong. Rule twelve
refuses to spend ₹12 chasing an expected ₹9. Every knob lives in one
dataclass, and a snapshot of the whole policy is entry zero of every audit
ledger — an auditor can always answer 'under which rules did the agent
act.'"

**[2:15–2:50] One case, end to end.**
SHOW: `python3 -m engine case case_00196` — scroll the timeline as you
narrate: DIAGNOSED → PLAYBOOK_ASSIGNED → two emails → voice call →
PROMISE_RECORDED → PROMISE_RESOLVED kept → CASE_RECOVERED.
SAY: "Here's one case's whole life. A ₹4.7 lakh invoice, diagnosed
lost-in-approvals, receivables ladder assigned. Two polite emails, then a
Hinglish voice call that lands a promise-to-pay. The promise is a tracked
object — checked on its due date, resolved kept, money in. Batch-wide:
nine promises made, nine kept. If this buyer had disputed instead, outreach
freezes instantly and a human gets the case within the hour."

**[2:50–3:20] The ledger, and me attacking it.**
SHOW: split screen — open `out/ledger.jsonl`, change ONE digit in any
entry, save. Run `python3 -m engine verify --ledger out/ledger.jsonl`. Let
the failure line fill the screen. Hold one second of silence.
SAY: "Every event you just saw — actions *and* refusals — is a row in an
append-only ledger where each entry carries the SHA-256 of the previous
one. Watch me cook the books: one digit, entry 900. Verify walks the chain—
and names the exact entry I touched. Compliance here isn't a PDF. It's
cryptography, and you just watched it catch me."

**[3:20–3:55] The baseline duel: proof it beats what exists.**
SHOW: `python3 -m engine baseline` — let the side-by-side table render;
point at the violations row, then the breakdown.
SAY: "Claiming to beat a dumb retry cron is cheap, so the repo ships the
cron. Same world, same seed, same priors — only the brain differs. The
cron: 40 percent recovery, two and a half thousand customer contacts,
7,195 policy violations — quiet hours, consent, contact caps, dunning a
disputing buyer, debiting mandates with no notice. Sanjeevani: 57 percent,
314 contacts, zero violations — and that zero is measured by the same
ground-truth auditor that scored the cron, not asserted. The delta is
exactly what diagnosis plus guardrails add: thirty lakh more, at an eighth
of the customer annoyance."

**[3:55–4:30] Real time, live on camera: `serve`.**
SHOW: `python3 -m engine serve --time-scale 60` already running in one
pane; Razorpay test dashboard in another. Fail a test payment (or have one
failed seconds before this beat — see pre-flight). Cut to the terminal as
the events stream in: `CASE_DETECTED` → `DIAGNOSED` → `PLAYBOOK_ASSIGNED`
→ (a minute later) `ACTION_EXECUTED` with a real `rzp.io` link. Open the
link — live Razorpay checkout — pay with a test card, and let
`CASE_RECOVERED` print.
SAY: "Everything so far was the evaluation harness — two hundred cases
proving the decisions are good. This is the product. Serve is the *same
engine class* — same diagnosis, same thirteen guardrails, same hash chain —
pointed at real test-mode Razorpay. I just failed a real payment... and
there it is: detected, the gateway error mapped to a root cause, playbook
assigned. The virtual clock runs at sixty-ex so the one-hour step fires in
a minute — guardrails still check it, fast-forwarded, never bypassed. And
that's a real payment link it just created. I pay it... case recovered,
money confirmed by Razorpay, on the chain. Structurally safe: the client
refuses any non-test key, and money only counts when Razorpay confirms it."

**[4:30–5:00] Verification and close.**
SHOW: `python3 -m unittest discover -s tests` → `Ran 53 tests ... OK`; then
`out/dashboard.html` in the browser, toggle dark mode, end on the hero
number.
SAY: "Fifty-three tests, including batch-wide invariants — no contact ever
lands in quiet hours, a risk-declined payer is never contacted, same seed
gives the same ledger head hash. Everything I've shown you is one clone and
one command away: measured recovery, a beaten baseline, compliant
escalation, stopping rules, and an audit trail that fights back.
Sanjeevani — diagnose first, dose carefully, stop when it isn't working,
and write everything down. Thank you."

## What broke, and how I got out (form field — honest war stories)

1. **The playbooks silently outran the clock.** Step delays are relative to
   the *previous* step, but I'd tuned them like absolute offsets — so the B2B
   voice call landed on day 14 of a 14-day horizon and the batch produced
   **zero promises-to-pay**. The metrics caught it (`promises: 0` looked
   impossible against a 0.45 prior), the ledger showed the steps never firing,
   and re-deriving cadences as relative deltas fixed it. Recovery jumped from
   20% to 57% — the single highest-leverage bug of the build.
2. **My own compliance rule ate my second retry.** The mandate playbook's
   second re-presentation was scheduled 124h after its fresh notice — past the
   72h staleness window I had *myself* written into policy. The guardrail
   skipped it with `predebit_stale`, exactly as designed: the audit trail
   flagged my bug as a policy violation. I fixed the cadence, not the rule.
3. **The EV stopping rule never fired.** With realistic priors, digital
   channels are so cheap that cost rarely exceeded expected value — the rule
   was dead code in demos. Instead of inflating costs, I added the
   small-ticket tier and let attempt fatigue decay the priors; now both
   economic stops fire and are visible in the guardrail report.

## Before you record — status of the two proof upgrades

Both are now built and woven into the script above. One setup step remains
for the video:

- [x] **Dumb-dunning baseline** — `engine/baseline.py`, run with
  `python3 -m engine baseline`. Same world, same seed; the cron manages
  40.1% recovery with 7,195 policy violations (quiet hours, caps, consent,
  dispute freezes, missing pre-debit notices) against Sanjeevani's 57.2%
  with zero — both scored by the same ground-truth auditor, and all of it
  covered by tests. Already in the cold open.
- [x] **Razorpay test-mode adapter** — `engine/razorpay_world.py`: the same
  one-method seam creating real test-mode payment links and orders, with
  money recognized only from signature-verified webhooks, a structural
  `rzp_test_*`-only guard, and mocked-transport tests. **To demo it on
  camera:** create free test-mode keys (razorpay.com → Test Mode → Settings
  → API Keys), `export RAZORPAY_KEY_ID=... RAZORPAY_KEY_SECRET=...`, then
  run `python3 -m engine razorpay smoke` — do this once before recording the
  3:55 beat.

- [x] **Real-time mode** — `engine/live.py`, run with `python3 -m engine
  serve --time-scale 60`. The same engine class as a live daemon: polls
  test-mode Razorpay for failed payments (no public URL needed), diagnoses
  and treats them with real payment links, recognizes recovery from
  Razorpay-confirmed payment state, one continuous ledger chain across
  restarts. This is the answer to "does it tackle the problem in real
  time" — demoed live in the 3:55 beat.

Pre-flight, 10 minutes before recording: run `python3 -m engine demo`
(fresh `out/`), pick which ledger entry you'll edit in the tamper demo and
practice the edit-save-verify motion once, run `python3 -m engine baseline`
once so the table isn't a surprise, keep `diagnose.py`, `playbooks.py`,
`guardrails.py`, `razorpay_world.py` open in editor tabs in that order, and
for the serve beat: start `serve --time-scale 60` a few minutes early,
rehearse failing a test payment once (the card-transplant first step fires
one virtual hour = one wall minute after detection), and keep the Razorpay
test dashboard logged in on a second screen.
