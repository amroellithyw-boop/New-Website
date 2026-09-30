# Getting started: from nothing to a running practice, step by step

This is the one document to follow. It assumes a Windows computer and no
programming knowledge. Mac differences are noted where they exist. Every
command is typed into the black ForgeOS window that `forge.cmd` opens, then
Enter. Nothing here is theoretical: each step was run on a fresh machine
before it was written down.

## Part A. Install (once, about 20 minutes)

1. **Python.** Go to python.org/downloads/windows, click the big Download
   button, open the file. On the first screen tick **Add python.exe to
   PATH**. Click Install Now. (Mac: python.org/downloads/macos, run the
   installer.)
2. **GitHub Desktop.** Install from desktop.github.com and sign in with the
   GitHub account that holds the repository.
3. **Clone.** In GitHub Desktop: File, Clone repository, choose
   `amroellithyw-boop/New-Website`, Clone. Then click **Current branch** at
   the top and choose `claude/serene-feynman-261gor`.
4. **Open the folder.** In File Explorer: Documents, GitHub, New-Website,
   services, forge-core.
5. **Run the installer.** Double-click **setup.cmd**. It installs, runs the
   self-test (you want `Release gate: PASS`), and asks for your keys one at
   a time. Press Enter to skip any you do not have yet. It also asks your
   firm name, your name and email; these go on every proposal and plan.
   (Mac: open Terminal, type `cd `, drag the forge-core folder into the
   window, Enter, then `bash setup.sh`.)
6. **Open the working window.** From now on, double-click **forge.cmd**.
   Type `forge` and Enter to see every command.

Everything you create lives in one folder: `forge-core\data`. Back that
folder up. It is never sent to GitHub.

## Part B. Connect your models and QuickBooks (10 minutes each, any time)

Models make reviews, extraction and drafting real. Without them everything
still runs, deterministically, and the model steps say "offline".

1. console.anthropic.com: Billing, add a card. API Keys, Create Key. Copy
   it.
2. console.x.ai: add billing, API Keys, Create API Key. Copy it.
3. Type `forge setup`, press Enter to keep what is already set, paste each
   key when asked.
4. `forge providers` shows two vendors marked set and which model each
   risk tier will use.

QuickBooks is in `docs/11-handover-checklist.md` section 1. Until its entity
mappers are finished, ledger commands run on the demonstration company and
say so.

## Part C. Your first client (10 minutes)

Every client is one folder under `data\clients\<id>`. The id is short, no
spaces: `acme`, `jane-fourplex`.

1. **Create the client.**

   ```
   forge client new jane-fourplex --name "Jane Example, 12 Sample Road" --naics 531110
   ```

   If you have your intake questionnaire as a file:
   `forge client new acme --intake acme-intake.json`.

2. **Give it the signed engagement letter.** PDF or text. It reads the
   phases, fees, what signing authorised, the deliverables, and works out
   what kind of engagement it is.

   ```
   forge client engage jane-fourplex "C:\Users\you\Downloads\Engagement Letter signed.pdf"
   ```

   It prints the phases with fees, the documents to request from the client
   (with the reason for each), the questions to ask, and the services it
   turned on. If the letter is not in your template it says so and, with a
   model key set, reads it anyway. To force the type:
   `--type monthly_bookkeeping`. The types are development_rental,
   rental_property, monthly_bookkeeping, cleanup, sales_tax, payroll,
   year_end_corporate, cfo_advisory, personal_tax.

3. **Send the client the two lists it printed.** Documents to request, and
   questions to answer. Ask them to answer questions by number: "Q1: yes".

## Part D. Every document and every reply (this is the habit)

Whatever a client sends, it goes in the same way. Save the attachment
anywhere, then:

```
forge client add jane-fourplex "C:\Users\you\Downloads\appraisal.pdf" --source email
forge client add jane-fourplex statement-march.pdf statement-april.pdf --source portal
```

For each file it prints what kind it is, the figures it read (appraised
value, loan limit and rate, statement period and closing balance, invoice
total and tax, rent, unit count), which requested document it satisfies, and
what it proposes to change in the engagement's facts. The same file sent
twice is recognised and not stored twice.

When the client replies by email, copy the text of the reply and:

```
forge client reply jane-fourplex "Q1: Yes, both of us are citizens. Q4: the property manager, Northgate." --subject "Re: your questions"
```

or save the email as a text file and use `--file`. Lines that start with a
question number close that question. With a model key set, unlabelled
answers are matched too, and facts stated in passing are recorded.

Something you learned on a call:

```
forge client answer jane-fourplex Q2 "Both owners will register; lawyer confirms co-ownership, not partnership" --by phone
forge client ask jane-fourplex "When does the lender expect the first draw request?"
```

Then, once a week or whenever documents arrive:

```
forge client facts jane-fourplex            # what the documents propose
forge client facts jane-fourplex --apply    # accept the proposals into the facts file
forge client status jane-fourplex           # everything known, everything still owed
```

Nothing a document says changes the numbers a plan runs on until you apply
it. That is deliberate: the model reads, you decide.

## Part E. The work itself

**Any client, any month.**

```
forge client run jane-fourplex --period-end 2027-04-30
```

Controls, health, cash, close checklist, sales tax return, provision,
deadlines and drafted actions, using that client's own knowledge and
learning. For a development or rental engagement it also writes the action
plan into the client's folder once the facts file has its figures.

**The whole firm, every morning.**

```
forge run-all data\clients
```

Every client, the daily brief, the queue in priority order.

**Closing, tax, briefing.** `forge close`, `forge tax return`,
`forge tax calendar`, `forge brief`, each with `--profile
data\clients\<id>\profile.json`.

**Teaching it.** Every time you accept or dismiss a finding:

```
forge outcome FOS-R027:CUST-0007 accepted --data-dir data\clients\jane-fourplex --by amro
```

Three dismissals of one pattern on one client suppress it there; one
acceptance lowers its review depth. Critical findings never go quiet.

**Selling.** `forge sales add`, `forge sales outreach`, `forge brief
--proposal`. Section 4 of `docs/11-handover-checklist.md`.

## Part F. A real-estate engagement, end to end

1. `forge client new` and `forge client engage` with the letter (Part C).
   The engagement type `development_rental` seeds a facts file at
   `data\clients\<id>\facts\realestate.json`.
2. Add the purchase agreement, the loan commitments, the appraisal, the
   permit and the bank statements as they arrive (Part D). Apply the
   proposals.
3. Open the facts file in Notepad and fill whatever is still empty:
   `forge client facts <id>` lists the empty fields by name. Dates are
   `YYYY-MM-DD`, money is digits with two decimals, the buildings go under
   `complexes` with their unit counts and expected values.
4. `forge client run <id>` writes the eleven-section plan. Or run any part
   on its own with the facts file:

   ```
   forge engagement hst data\clients\<id>\facts\realestate.json
   forge engagement ownership data\clients\<id>\facts\realestate.json
   forge engagement residency data\clients\<id>\facts\realestate.json
   forge engagement project data\clients\<id>\facts\realestate.json
   forge engagement rental data\clients\<id>\facts\realestate.json
   forge engagement pack data\clients\<id>\facts\realestate.json
   forge engagement chart
   ```

5. Read the plan's section 10, the questions to settle, before the meeting.

## Part G. When something goes wrong

- **A command says "no client".** The id is the folder name under
  `data\clients`. `forge client list` shows them.
- **The letter parsed no phases.** Your letter is not in the template
  layout. Set a model key (Part B) and run `forge client engage` again, or
  pass `--type`.
- **A document was classified wrongly.** Add it again with the right hint in
  the filename (`statement`, `invoice`, `appraisal`, `lease`), or set a
  model key; the model's kind wins when the words are ambiguous.
- **`forge client facts` shows nothing but the plan is wrong.** Open the
  facts file; the figure is either empty or yours to correct. The plan never
  invents a number.
- **Python is not found.** Run the Python installer again and tick the PATH
  box.
- Anything else: screenshot the black window and send it.

## Part H. What to do this week, in order

1. Part A, then `forge bench` shows PASS.
2. Part C and D with your current engagement letter and whatever the client
   has already sent. Ten minutes, and you have a client folder that knows
   more than your email does.
3. Part B, two keys.
4. The QuickBooks sandbox in `docs/11-handover-checklist.md`, and send the
   tie-out screenshot.
