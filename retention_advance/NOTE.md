# Submission note: retention and advance recovery (Odoo 19)

Module: `retention_advance` (depends on `account`, `sale`). No Odoo core file is changed.
Author: Vyshnav

## Journal entries the module creates
All are `entry` moves in the Retention Journal (setting; default = first miscellaneous journal).
Example: work 100,000, VAT 15%, retention 10%, advance recovery 20%.

| When | Entry | Reconciliation |
|---|---|---|
| Invoice posted (retention) | Dr Retention Receivable 10,000 (maturity = handover date) / Cr Receivable 10,000 | Cr line with the invoice term line |
| Invoice posted (advance) | Dr Receivable 20,000 / Cr Receivable 20,000 | Cr line with the invoice; Dr line with the advance payment line |
| Release retention | Dr Receivable X (maturity = release date) / Cr Retention Receivable X | Cr line with the held retention line (full or partial) |
| Credit note posted | Dr Receivable r / Cr Retention Receivable r, and Dr Receivable a / Cr Receivable a ("advance restored", left open) | Dr lines with the credit note; Cr retention line with the held retention |
| Invoice reset to draft, or full cancel | Reversal of each helper entry above | Originals reconciled with their reversals |

Invoice itself is standard: Dr Receivable 115,000 / Cr Revenue 100,000 / Cr VAT 15,000.
After posting: due now 85,000, retention held 10,000, advance left 180,000.
Nothing posted is edited or deleted. Lock dates are checked for release and reversal dates.

## Assumptions
1. Project = sales order of the first invoice line that has one (editable in draft); no SO = customer only.
2. Retention and recovery % are on the untaxed amount.
3. Retention account must be reconcilable and not of type Receivable.
4. Credit-note retention return is capped at what is still held; restored advance comes back as an open
   customer credit, not re-attached to the original payment.
5. An invoice with released/reduced retention cannot be reset to draft until that is reversed.
6. Report is in company currency, one row per company/customer/project. An advance counts in the
   project set on the payment (set automatically when first used for a recovery).

## Not finished / known limits
- Not yet run on a live Odoo 19 Enterprise database; the tests in `tests/` still need to be executed.
- Invoice shows "Partially Paid" after posting (settled by reconciliation); `amount_residual` = amount to pay now.
- Released retention is an open customer line, not shown in the invoice's own payment widget.
- Excel totals mix currencies if companies with different currencies are selected.
