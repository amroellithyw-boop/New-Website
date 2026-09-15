# Handover checklist: the six things only you can supply, step by step

Each section says what I already did, what you do, and how we both know it
worked. Do them in order; the first one matters most.

## 0. Getting the software onto your computer (once, no coding)

**Windows**

1. Install Python: https://www.python.org/downloads/windows/, click the big
   Download button, run it, and on the first screen tick **Add python.exe to
   PATH** before clicking Install Now.
2. Install GitHub Desktop: https://desktop.github.com. Sign in with your
   GitHub account. **File**, **Clone repository**, pick
   `amroellithyw-boop/New-Website`, and in **Current branch** choose
   `claude/serene-feynman-261gor`. Note the folder it cloned into, usually
   `Documents\GitHub\New-Website`.
3. In File Explorer open that folder, then `services`, then `forge-core`.
   Double-click **setup.cmd**. A black window opens, installs everything,
   runs the self-test (you want to see `Release gate: PASS`), then asks you
   for each key one at a time. Paste and press Enter. Leave anything you do
   not have blank.
4. From then on, double-click **forge.cmd** in the same folder to get a
   window where you type `forge` commands.

**Mac**

1. Install Python from https://www.python.org/downloads/macos/.
2. Install GitHub Desktop and clone the repository as above.
3. Open Terminal, type `cd ` (with a space), drag the `forge-core` folder into
   the Terminal window, press Enter, then type `bash setup.sh` and Enter.
4. From then on, in Terminal: `cd` to the folder and `source .venv/bin/activate`.

`.env` and `firm.json` are written by the setup and ignored by git. They never
leave your machine. To change a key later, run `forge setup` again.

---

## 1. QuickBooks sandbox app and one real client file

**Already done:** `forge qbo connect` does the whole OAuth dance and keeps the
tokens in `data/qbo/`. `forge qbo pull --anonymise` pulls a company to one
file with every customer, vendor and employee renamed consistently and all
contact details removed. `forge qbo tieout --from-file` replays that file with
no network and reports exactly which entities the mappers still miss.

**You do (about 15 minutes):**

1. Go to https://developer.intuit.com and sign in with your QuickBooks
   login, or create a developer account.
2. Top menu: **Dashboard**, then **Create an app**. Choose **QuickBooks
   Online and Payments**. Name it `ForgeOS`. Scope:
   `com.intuit.quickbooks.accounting`. Create it.
3. Left menu: **Keys and credentials**. Turn on **Show credentials**. You
   will paste the Client ID and Client secret when `setup.cmd` (or
   `forge setup`) asks for them.
4. On the same page, under **Redirect URIs**, click **Add URI** and enter
   exactly `http://localhost:8765/callback`. Save.
5. Left menu: **Sandbox** (under API Docs & Tools). Intuit created a sample
   company for you already. Note its name.
6. In the terminal:

   ```bash
   forge qbo connect --tenant sandbox
   ```

   A browser opens. Sign in, pick the sandbox company, click **Connect**. The
   terminal says "Connected".

7. Pull it and tie it out:

   ```bash
   forge qbo pull --tenant sandbox --out fixtures/sandbox.json --period-end 2026-06-30 --months 12
   forge qbo tieout --from-file fixtures/sandbox.json
   ```

   It will print the tie-out and a list of "normalisation is not implemented"
   gaps. That list is what I build next.

**The real client file (another 15 minutes, after the sandbox works):**

8. Back on the developer portal, **Keys and credentials** has a
   **Production** tab. Intuit asks a few compliance questions before it
   shows production keys; answer them. Then run `forge setup --production`
   and paste the production keys; it writes `.env.production`.
9. Pick one client whose books you know have problems. Run:

   ```bash
   forge --env .env.production qbo connect --tenant clientA
   forge --env .env.production qbo pull --tenant clientA --out fixtures/clientA.json --months 12 --anonymise
   ```

10. Open `fixtures/clientA.json` once and scan it. Names read "Customer
    0001", "Vendor 0007". Emails, phones, addresses and tax numbers are gone.
    Amounts and dates are untouched.
11. Copy `fixtures/known-defects.template.md` to
    `fixtures/clientA.defects.md` and fill in what you already know is wrong.
    Ten lines is plenty.
12. Commit both files and push, or send them to me any other way:

    ```bash
    git add fixtures/clientA.json fixtures/clientA.defects.md
    git commit -m "Recorded client file for the bench"
    git push
    ```

**How we know it worked:** `forge qbo tieout --from-file fixtures/clientA.json`
runs, and prints a tie-out with a list of mapping gaps rather than an error.

---

## 2. Two vendor API keys, and Ollama

**Already done:** `forge providers` shows every vendor, whether its key is
present, and which model each risk tier will use. `.env` is read
automatically.

**You do (10 minutes):**

1. Anthropic: https://console.anthropic.com, sign in, **Billing**, add a
   card. **API Keys**, **Create Key**, name it `forgeos`. Copy it once; it
   is not shown again.
2. xAI: https://console.x.ai, sign in, add billing, **API Keys**, **Create
   API Key**. Copy it.
3. Run `forge setup` and paste each key when asked. Press Enter to keep
   anything already set.
4. Optional, free local model: install Ollama from https://ollama.com, then
   in a terminal:

   ```bash
   ollama pull qwen2.5:14b
   ```

   Leave `OLLAMA_BASE_URL` in `.env` as it is.

5. Check:

   ```bash
   forge providers
   ```

   You want at least two families listed and a different family named for
   the adversary role than for the preparer.

6. First real call, cheap and low risk:

   ```bash
   forge onboard intake.json --plan
   ```

   It prints the plan and the cost. If a vendor rejects a field name, the
   error names the vendor and the field; send it to me.

**How we know it worked:** `forge providers` shows both keys as set and
`--plan` returns a plan with a cost under a cent.

---

## 3. 2026 payroll constants

**Already done:** I cross-checked every constant against published 2026
tables and recorded the check in the code:

| Item | In the code | Published |
| --- | --- | --- |
| CPP maximum pensionable earnings | 74,600 | 74,600 |
| CPP basic exemption | 3,500 | 3,500 |
| CPP rate | 5.95% | 5.95% |
| CPP maximum contribution (derived) | 4,230.45 | 4,230.45 |
| CPP2 ceiling | 85,000 | 85,000 |
| CPP2 rate, maximum (derived) | 4%, 416.00 | 4%, 416.00 |
| EI rate, maximum insurable | 1.63%, 68,900 | 1.63%, 68,900 |
| EI maximum, employer 1.4x (derived) | 1,123.07, 1,572.30 | 1,123.07, 1,572.30 |
| Federal brackets | 58,523 / 117,045 / 181,440 / 258,482 | same |
| Federal rates, BPA | 14 / 20.5 / 26 / 29 / 33, 16,452 | same |
| Ontario brackets | 53,891 / 107,785 / 150,000 / 220,000 | same |
| Ontario rates, BPA | 5.05 / 9.15 / 11.16 / 12.16 / 13.16, 12,989 | same |
| Ontario surtax thresholds | 5,818 and 7,446 | same |
| Ontario Health Premium bands | unchanged | unchanged |

Tests now assert the four derived maxima, so a wrong constant fails the
build. The CRA site itself is blocked from where I run, so the one thing I
could not do is open the T4127 PDF.

**You do (5 minutes):**

1. Open https://www.canada.ca/en/revenue-agency/services/forms-publications/payroll/t4127-payroll-deductions-formulas.html
   and open the current 2026 edition.
2. Find Table 8.1 (federal) and the Ontario table. Compare the six threshold
   figures and the two basic personal amounts to the table above.
3. If every figure matches, tell me "payroll confirmed". If one differs,
   tell me which.

**How we know it worked:** you say so, and I change one line to record
"T4127 confirmed by Amro on <date>".

---

## 4. Pricing, firm name, sender name

**Already done:** these live in `firm.json`, not in code. `forge firm`
shows what is in use. The proposal, outreach and diagnostic fee all read it.

**You do (5 minutes):**

1. `setup.cmd` already asked for your firm name, your name and email and
   wrote `firm.json` in the `forge-core` folder. Open it in Notepad.
2. Check `name`, `sender`, `email`; add `phone` and `website` if you like.
3. Change any price you disagree with. Monthly base by client size, monthly
   add-on per service, per-employee payroll, per-100-transactions volume,
   per-month-behind cleanup, and the diagnostic fee by size. Delete any line
   you want left at the default.
4. Check:

   ```bash
   forge firm
   forge brief --proposal
   ```

**How we know it worked:** the proposal ends with your name and shows your
prices.

---

## 5. Hosting in a Canadian region

**Already done:** a `Dockerfile` builds the whole system into one container
that runs every command:

```bash
docker build -t forgeos services/forge-core
docker run --rm forgeos bench
```

Nothing in the system needs a database or a server yet. It runs on files.

**You do (one decision, then tell me):**

Pick one. My recommendation is the first.

| Option | Where | Monthly cost to start | Why |
| --- | --- | --- | --- |
| **Your own laptop or a Mac mini in the office** | Ontario | $0 | The system is files plus a CLI. Until there is a second user or a client-facing page, a machine you control in the province is the simplest data-residency story there is. |
| Fly.io, region `yyz` (Toronto) | Toronto | about $5 | One command deploys the Dockerfile. Good when you want it to run on a schedule without your laptop open. |
| AWS `ca-central-1` (Montreal) or Azure Canada Central (Toronto) | Canada | $20 to $50 | The answer for when there is a client portal and an auditor asking questions. Not needed yet. |

Whichever you pick, the data directory (`data/`, `clients/`, `fixtures/`)
is the only thing that needs backing up.

**How we know it worked:** you tell me the option, and if it is not the
laptop I write the deploy script for it.

---

## 6. Legal review of scope language

**Already done:** every sentence a client or prospect will read is collected
in `docs/12-scope-language-for-legal-review.md`, with where it appears, what
it is for, and the specific questions to ask about each. It covers the report
footer, the proposal, outreach (CASL), collection emails (Ontario collection
rules) and data handling (PIPEDA).

**You do (15 minutes plus the lawyer's time):**

1. Send `docs/12-scope-language-for-legal-review.md` to a lawyer who does
   professional services engagements in Ontario. If you do not have one, the
   Law Society of Ontario referral service gives a free half-hour:
   https://lso.ca/public-resources/finding-a-lawyer-or-paralegal/law-society-referral-service
2. Ask for redlines on the quoted text only.
3. Send me the redlines. I change the text and the tests keep it there.

**How we know it worked:** the report footer and proposal print the reviewed
wording.

---

## The order that unblocks the most

1. Section 1, sandbox only (15 minutes). This is what lets me finish the
   entity mappers.
2. Section 2 (10 minutes). This is what makes reviews real.
3. Section 4 (5 minutes). Your name on the proposals.
4. Section 1, real client file (15 minutes). This is what makes the bench
   real.
5. Section 3 (5 minutes). Before the first live payroll.
6. Section 5 (one word).
7. Section 6 (whenever the lawyer gets back).
