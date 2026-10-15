# retention_advance — Contractor Retention & Advance Recovery (Odoo 19)

Depends on `account` and `sale`. Nothing in Odoo core is modified.

## Accounting design

The customer invoice itself stays a **standard Odoo invoice** (VAT on the full work done, full
receivable of 115,000 in the example). When it is posted the module creates one or two *helper
journal entries* in the Retention Journal and reconciles them with the invoice. Posted entries are
never edited or deleted — anything that has to be undone is **reversed**.

| Step | Helper entry | Effect |
|---|---|---|
| Post invoice (retention) | Dr Retention Receivable 10,000 (due = handover date) / Cr Receivable 10,000, reconciled with the invoice | invoice residual drops by 10,000 |
| Post invoice (advance) | Dr Receivable 20,000 / Cr Receivable 20,000; the Dr line is reconciled with the advance payment, the Cr line with the invoice | invoice residual drops by 20,000, advance residual drops by 20,000 |
| Release retention | Dr Receivable / Cr Retention Receivable, Cr line reconciled with the held retention (full or partial) | amount becomes a normal open receivable due on the release date |
| Credit note (posted) | proportional Dr Receivable / Cr Retention (reconciled with the held retention) and Dr Receivable / Cr Receivable "advance restored" | retention and recovered advance go back in proportion |
| Reset invoice to draft | helper entries are reversed and the reversals reconciled with them | re-posting creates fresh entries, nothing duplicated |
| Full cancel (credit note wizard) | helper entries are reversed first, then Odoo nets invoice and credit note | everything returns to zero, advance fully available again |

Invoice 100,000 + 15% VAT, 10% retention, 20% recovery → amount due now **85,000**
(`amount_residual`), **10,000** on Retention Receivable, advance balance **180,000**.

## Setup
Accounting ▸ Configuration ▸ Settings ▸ *Retention & Advance Recovery*: choose the Retention
Receivable account (reconcilable, **not** of type Receivable), default retention %, optional journal.

## Usage
* Customer invoice ▸ tab *Retention & Advance*: retention %, handover date, advance payment,
  recovery as % or fixed amount. Totals and *Amount to Pay Now* appear under the tax totals and on the PDF.
* Customers ▸ *Release Retention* (also in the partner *Action* menu): pick lines, amount, date.
* Reporting ▸ *Retention & Advance*: as-at report, **Export to Excel** button.
* Customer payments can be flagged *Advance Payment* with a *Project*; payments used for a recovery
  are flagged automatically (project defaults to the invoice's sales order).

## Assumptions / limits (please read)
1. **Project** = the sales order of the first invoice line that has one (editable in draft). No SO → customer-only group.
2. Retention % and recovery % apply to the **untaxed** amount (work done). VAT is untouched.
3. Retention account must not be of type Receivable (else Odoo adds it to the invoice residual).
4. Credit-note retention return is capped at what is still held; the restored advance returns as an
   open customer credit (not re-attached to the original payment record).
5. An invoice whose retention was already released (or reduced by a credit note) cannot be reset to
   draft until those entries are reversed.
6. After posting, the invoice shows *Partially Paid* because retention/advance are settled by
   reconciliation; `amount_residual` is the amount to pay now.
7. Released amounts become an open receivable line (due on the release date) on the customer, to be paid
   from the customer ledger / bank reconciliation; they are not attached to the invoice's own payment widget.
8. Report amounts are in company currency; multi-company rows are separate, Excel totals mix currencies
   if several companies with different currencies are selected.
9. Lock dates: invoices keep Odoo's date handling; helper entries use the invoice date, reversals use
   today (or later), release uses the chosen date and is refused if locked.
10. **Not run against a live Odoo 19 Enterprise instance in this environment.** Written against the 19.0
    source; run `odoo -i retention_advance --test-tags /retention_advance` first.
