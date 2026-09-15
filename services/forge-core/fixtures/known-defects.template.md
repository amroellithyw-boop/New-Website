# Known defects in <client name>, period <start> to <end>

One line per defect you already know about. Be specific: the more exact the
amount and the date, the more precisely the bench can score whether the
system found it.

| # | What is wrong | Where (account, vendor, customer, document number) | Amount | Date | How you know |
| --- | --- | --- | --- | --- | --- |
| 1 | Duplicate bill paid twice | Vendor: Vendor 0012, bills 4471 and 4471A | 2,340.00 | 2026-03 | Vendor refunded it |
| 2 | HST not charged on an invoice | Customer 0003, invoice 1088 | 13% of 8,200.00 | 2026-04-17 | Customer queried it |
| 3 | | | | | |

Things that look wrong but are actually fine (so a finding on them counts
against the system):

| # | What it looks like | Why it is fine |
| --- | --- | --- |
| 1 | | |
