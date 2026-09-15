# Fixtures: recorded QuickBooks files

Put anonymised QuickBooks exports here. They are produced by

```bash
forge qbo pull --tenant <name> --period-end 2026-06-30 --months 12 --anonymise --out fixtures/<name>.json
```

and replayed, with no network or token, by

```bash
forge qbo tieout --from-file fixtures/<name>.json
```

Every customer, vendor and employee name is replaced consistently everywhere it
appears, and emails, phones, addresses, tax numbers and notes are removed.
Amounts, dates, accounts and document numbers are untouched, because those are
what the controls test. Look through the file once before committing it.

Next to each export, list what you already know is wrong in the books in
`<name>.defects.md`, using `known-defects.template.md`. That list is what turns
ForgeBench from synthetic to real.
