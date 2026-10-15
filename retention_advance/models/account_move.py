from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_is_zero

RA_KINDS = [
    ('hold', 'Retention Held'),
    ('advance', 'Advance Recovery'),
    ('release', 'Retention Release'),
]
SALE_TYPES = ('out_invoice', 'out_refund')


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ------------------------------------------------------------------
    # Customer invoice / credit note fields
    # ------------------------------------------------------------------
    ra_retention_percent = fields.Float(
        string='Retention %', digits=(16, 4), copy=True,
        compute='_compute_ra_retention_percent', store=True, readonly=False, precompute=True,
        help="Percentage of the work done (untaxed amount) held back by the client.")
    ra_retention_due_date = fields.Date(
        string='Expected Handover Date', copy=True,
        help="Due date of the retention receivable line.")
    ra_advance_payment_id = fields.Many2one(
        'account.payment', string='Advance Payment', copy=True, ondelete='restrict',
        index='btree_not_null',
        help="Customer payment (no invoice) from which the advance is recovered.")
    ra_recovery_type = fields.Selection(
        [('percent', 'Percentage of work done'), ('fixed', 'Fixed amount')],
        string='Recovery Method', default='percent', copy=True)
    ra_recovery_percent = fields.Float(string='Recovery %', digits=(16, 4), copy=True)
    ra_recovery_fixed = fields.Monetary(
        string='Recovery Amount (fixed)', currency_field='currency_id', copy=True)
    ra_project_id = fields.Many2one(
        'sale.order', string='Project', compute='_compute_ra_project_id',
        store=True, readonly=False, index='btree_not_null', copy=False,
        help="Sales order the invoice was created from. Can be changed while in draft.")

    ra_retention_amount = fields.Monetary(
        string='Retention', currency_field='currency_id',
        compute='_compute_ra_amounts', store=True)
    ra_recovery_amount = fields.Monetary(
        string='Advance Recovery', currency_field='currency_id',
        compute='_compute_ra_amounts', store=True)
    ra_amount_to_pay = fields.Monetary(
        string='Amount to Pay Now', currency_field='currency_id',
        compute='_compute_ra_amounts', store=True)
    ra_advance_available = fields.Monetary(
        string='Advance Balance Available', currency_field='currency_id',
        compute='_compute_ra_advance_available')

    # ------------------------------------------------------------------
    # Fields on the helper journal entries created by this module
    # ------------------------------------------------------------------
    ra_kind = fields.Selection(RA_KINDS, string='Retention Entry Type', copy=False, index=True)
    ra_source_move_id = fields.Many2one(
        'account.move', string='Source Invoice', copy=False, index='btree_not_null', ondelete='restrict')
    ra_companion_ids = fields.One2many('account.move', 'ra_source_move_id', string='Retention Entries')
    ra_signed_amount = fields.Monetary(
        string='Retention Amount (company currency)', currency_field='company_currency_id', copy=False,
        help="Positive = held / recovered / released. Negative = credit note or reversal.")
    ra_partner_id = fields.Many2one('res.partner', string='Retention Customer', copy=False, index=True)
    ra_reversed = fields.Boolean(copy=False, index=True)
    ra_entry_payment_id = fields.Many2one('account.payment', copy=False)

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('move_type', 'company_id')
    def _compute_ra_retention_percent(self):
        for move in self:
            if move.move_type == 'out_invoice':
                move.ra_retention_percent = (
                    move.ra_retention_percent or move.company_id.ra_default_retention_percent)
            else:
                move.ra_retention_percent = 0.0

    @api.depends('invoice_line_ids.sale_line_ids.order_id', 'reversed_entry_id.ra_project_id', 'move_type')
    def _compute_ra_project_id(self):
        for move in self:
            if move.move_type not in SALE_TYPES:
                move.ra_project_id = move.ra_project_id
                continue
            orders = move.invoice_line_ids.sale_line_ids.order_id
            move.ra_project_id = orders[:1] or move.reversed_entry_id.ra_project_id

    @api.depends(
        'move_type', 'amount_untaxed', 'amount_total', 'currency_id',
        'ra_retention_percent', 'ra_recovery_type', 'ra_recovery_percent', 'ra_recovery_fixed',
        'reversed_entry_id.amount_untaxed',
        'reversed_entry_id.ra_retention_amount', 'reversed_entry_id.ra_recovery_amount')
    def _compute_ra_amounts(self):
        for move in self:
            retention = recovery = 0.0
            cur = move.currency_id
            if move.move_type == 'out_invoice':
                retention = cur.round(move.amount_untaxed * move.ra_retention_percent / 100.0)
                if move.ra_recovery_type == 'fixed':
                    recovery = cur.round(move.ra_recovery_fixed)
                else:
                    recovery = cur.round(move.amount_untaxed * move.ra_recovery_percent / 100.0)
            elif move.move_type == 'out_refund' and move.reversed_entry_id.amount_untaxed:
                # Credit note: proportional to the invoice it reverses.
                orig = move.reversed_entry_id
                ratio = min(move.amount_untaxed / orig.amount_untaxed, 1.0)
                retention = cur.round(orig.ra_retention_amount * ratio)
                recovery = cur.round(orig.ra_recovery_amount * ratio)
            move.ra_retention_amount = retention
            move.ra_recovery_amount = recovery
            move.ra_amount_to_pay = move.amount_total - retention - recovery

    @api.depends('ra_advance_payment_id', 'currency_id', 'date')
    def _compute_ra_advance_available(self):
        for move in self:
            move.ra_advance_available = move._ra_advance_available() if move.ra_advance_payment_id else 0.0

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('ra_retention_percent', 'ra_recovery_percent', 'ra_recovery_fixed')
    def _check_ra_values(self):
        for move in self.filtered(lambda m: m.move_type == 'out_invoice'):
            if not 0.0 <= move.ra_retention_percent <= 100.0:
                raise ValidationError(_("Retention % must be between 0 and 100."))
            if not 0.0 <= move.ra_recovery_percent <= 100.0:
                raise ValidationError(_("Recovery % must be between 0 and 100."))
            if move.ra_recovery_fixed < 0:
                raise ValidationError(_("The fixed recovery amount cannot be negative."))

    @api.constrains('ra_recovery_amount', 'ra_advance_payment_id')
    def _check_ra_recovery_available(self):
        for move in self.filtered(lambda m: m.state == 'draft' and m.move_type == 'out_invoice'
                                  and m.ra_recovery_amount and m.ra_advance_payment_id):
            available = move._ra_advance_available()
            if move.currency_id.compare_amounts(move.ra_recovery_amount, available) > 0:
                raise ValidationError(_(
                    "You cannot recover more than what is left on the advance payment %(pay)s: "
                    "%(available)s available, %(asked)s requested.",
                    pay=move.ra_advance_payment_id.display_name,
                    available=move.currency_id.format(available),
                    asked=move.currency_id.format(move.ra_recovery_amount)))

    def write(self, vals):
        guarded = {'ra_retention_percent', 'ra_retention_due_date', 'ra_advance_payment_id',
                   'ra_recovery_type', 'ra_recovery_percent', 'ra_recovery_fixed'}
        if guarded & set(vals):
            if any(m.state == 'posted' and m.move_type in SALE_TYPES for m in self):
                raise UserError(_("Retention and advance recovery cannot be changed on a posted "
                                  "invoice. Reset it to draft first."))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Advance helpers
    # ------------------------------------------------------------------
    def _ra_advance_lines(self):
        """Open credit receivable lines of the selected advance payment."""
        self.ensure_one()
        return self.ra_advance_payment_id.move_id.line_ids.filtered(
            lambda l: l.parent_state == 'posted'
            and l.account_id.account_type == 'asset_receivable'
            and l.amount_residual < 0)

    def _ra_advance_available(self):
        """Remaining advance expressed in the invoice currency."""
        self.ensure_one()
        lines = self._ra_advance_lines()
        if not lines:
            return 0.0
        if all(l.currency_id == self.currency_id for l in lines):
            return sum(-l.amount_residual_currency for l in lines)
        return self.company_currency_id._convert(
            sum(-l.amount_residual for l in lines), self.currency_id, self.company_id,
            self.date or self.invoice_date or fields.Date.context_today(self))

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------
    def _ra_validate_before_post(self):
        used = {}
        for move in self:
            if move.move_type != 'out_invoice':
                continue
            cur = move.currency_id
            if move.ra_retention_amount:
                if not move.company_id.ra_retention_account_id:
                    raise UserError(_("Select the Retention Receivable account in the Accounting "
                                      "settings before posting invoice %s.", move.display_name))
                if not move.ra_retention_due_date:
                    raise UserError(_("Enter the expected handover date for the retention of "
                                      "invoice %s.", move.display_name))
            if move.ra_recovery_amount:
                pay = move.ra_advance_payment_id
                if not pay:
                    raise UserError(_("Select the advance payment to recover from on invoice %s.",
                                      move.display_name))
                if pay.company_id != move.company_id or \
                        pay.partner_id.commercial_partner_id != move.commercial_partner_id:
                    raise UserError(_("The advance payment %(pay)s does not belong to customer "
                                      "%(partner)s in company %(company)s.",
                                      pay=pay.display_name, partner=move.commercial_partner_id.name,
                                      company=move.company_id.name))
                available = move._ra_advance_available() - used.get(pay.id, 0.0)
                if cur.compare_amounts(move.ra_recovery_amount, available) > 0:
                    raise UserError(_(
                        "Invoice %(inv)s recovers %(asked)s but only %(avail)s is left on advance %(pay)s.",
                        inv=move.display_name, asked=cur.format(move.ra_recovery_amount),
                        avail=cur.format(available), pay=pay.display_name))
                used[pay.id] = used.get(pay.id, 0.0) + move.ra_recovery_amount
            if cur.compare_amounts(move.ra_amount_to_pay, 0.0) < 0:
                raise UserError(_("The retention and advance recovery of invoice %s exceed the "
                                  "invoice total.", move.display_name))

    def _post(self, soft=True):
        ra_moves = self.filtered(
            lambda m: m.state == 'draft' and m.move_type in SALE_TYPES
            and (m.ra_retention_amount or m.ra_recovery_amount)
            and not self.env.context.get('ra_skip_companions'))
        ra_moves._ra_validate_before_post()
        posted = super()._post(soft=soft)
        ra_moves.filtered(lambda m: m.state == 'posted')._ra_create_companions()
        return posted

    def _ra_active_companions(self, kind=None):
        self.ensure_one()
        return self.ra_companion_ids.filtered(
            lambda c: c.state == 'posted' and not c.ra_reversed and (not kind or c.ra_kind == kind))

    def _ra_create_companions(self):
        for move in self:
            if move._ra_active_companions():
                continue  # already done: never duplicate
            if move.move_type == 'out_invoice':
                move._ra_post_invoice_companions()
            elif move.reversed_entry_id:
                move._ra_post_refund_companions()

    # -- building blocks -------------------------------------------------
    def _ra_term_lines(self):
        return self.line_ids.filtered(
            lambda l: l.display_type == 'payment_term' and l.account_id.account_type == 'asset_receivable')

    def _ra_split(self, amount, total_balance=None):
        """Spread `amount` (invoice currency, > 0) over the receivable term lines pro rata.

        Returns [(term_line, amount_currency, balance)] with positive numbers. Balances follow the
        rate of each term line unless `total_balance` is given (then it is spread by currency share).
        """
        self.ensure_one()
        terms = self._ra_term_lines()
        cur, ccur = self.currency_id, self.company_currency_id
        total_cur = sum(abs(l.amount_currency) for l in terms)
        result, rem_cur, rem_bal = [], amount, total_balance
        for i, term in enumerate(terms):
            last = i == len(terms) - 1
            share = rem_cur if last else min(
                cur.round(amount * abs(term.amount_currency) / total_cur), rem_cur)
            if float_is_zero(share, precision_rounding=cur.rounding):
                continue
            if total_balance is not None:
                bal = rem_bal if last else ccur.round(total_balance * share / amount)
                rem_bal -= bal
            elif cur.is_zero(abs(term.amount_currency) - share):
                bal = abs(term.balance)
            else:
                bal = ccur.round(abs(term.balance) * share / abs(term.amount_currency))
            rem_cur -= share
            result.append((term, share, bal))
        return result

    def _ra_line_cmd(self, account, amount_currency, balance, name, date_maturity=False):
        return Command.create({
            'account_id': account.id,
            'partner_id': self.commercial_partner_id.id,
            'name': name,
            'currency_id': self.currency_id.id,
            'amount_currency': amount_currency,
            'balance': balance,
            'date_maturity': date_maturity or False,
        })

    def _ra_make_companion(self, kind, line_cmds, signed_amount, name, advance=None):
        self.ensure_one()
        company = self.company_id
        comp = self.env['account.move'].with_company(company).create({
            'move_type': 'entry',
            'journal_id': company._ra_get_journal().id,
            'company_id': company.id,
            'date': self.date,
            'ref': '%s - %s' % (name, self.name),
            'ra_kind': kind,
            'ra_source_move_id': self.id,
            'ra_signed_amount': signed_amount,
            'ra_partner_id': self.commercial_partner_id.id,
            'ra_project_id': self.ra_project_id.id,
            'ra_entry_payment_id': advance.id if advance else False,
            'line_ids': line_cmds,
        })
        comp._post(soft=False)
        return comp

    # -- invoice ---------------------------------------------------------
    def _ra_post_invoice_companions(self):
        self.ensure_one()
        company = self.company_id
        if self.ra_retention_amount:
            name = _("Retention")
            pairs = self._ra_split(self.ra_retention_amount)
            tot_cur = sum(p[1] for p in pairs)
            tot_bal = sum(p[2] for p in pairs)
            cmds = [self._ra_line_cmd(t.account_id, -c, -b, name, t.date_maturity) for t, c, b in pairs]
            cmds.append(self._ra_line_cmd(
                company.ra_retention_account_id, tot_cur, tot_bal,
                _("Retention %s", self.name), self.ra_retention_due_date))
            comp = self._ra_make_companion('hold', cmds, tot_bal, name)
            lines = comp.line_ids.sorted('id')
            for (term, _c, _b), cr_line in zip(pairs, lines):
                (term + cr_line).reconcile()
        if self.ra_recovery_amount:
            name = _("Advance recovery")
            pairs = self._ra_split(self.ra_recovery_amount)
            tot_cur = sum(p[1] for p in pairs)
            tot_bal = sum(p[2] for p in pairs)
            account = pairs[0][0].account_id
            cmds = [self._ra_line_cmd(t.account_id, -c, -b, name, t.date_maturity) for t, c, b in pairs]
            cmds.append(self._ra_line_cmd(
                account, tot_cur, tot_bal, _("Advance recovery %s", self.name), self.date))
            comp = self._ra_make_companion('advance', cmds, tot_bal, name, advance=self.ra_advance_payment_id)
            lines = comp.line_ids.sorted('id')
            for (term, _c, _b), cr_line in zip(pairs, lines):
                (term + cr_line).reconcile()
            (self._ra_advance_lines() + lines[-1]).reconcile()
            pay = self.ra_advance_payment_id.sudo()
            pay.write({
                'ra_is_advance': True,
                'ra_project_id': pay.ra_project_id.id or self.ra_project_id.id,
            })

    # -- credit note -----------------------------------------------------
    def _ra_post_refund_companions(self):
        self.ensure_one()
        orig = self.reversed_entry_id
        company = self.company_id
        retention_account = company.ra_retention_account_id
        if self.ra_retention_amount and retention_account:
            hold = orig._ra_active_companions('hold')[:1]
            ret_line = hold.line_ids.filtered(lambda l: l.account_id == retention_account and l.balance > 0)
            if ret_line and ret_line.amount_residual_currency > 0:
                amount = min(self.ra_retention_amount, ret_line.amount_residual_currency)
                if self.currency_id.is_zero(amount - ret_line.amount_residual_currency):
                    ret_bal = ret_line.amount_residual
                else:
                    ret_bal = self.company_currency_id.round(
                        amount * ret_line.amount_residual / ret_line.amount_residual_currency)
                name = _("Retention returned")
                pairs = self._ra_split(amount, total_balance=ret_bal)
                cmds = [self._ra_line_cmd(t.account_id, c, b, name, t.date_maturity) for t, c, b in pairs]
                cmds.append(self._ra_line_cmd(
                    retention_account, -amount, -ret_bal, _("Retention returned %s", self.name),
                    ret_line.date_maturity))
                comp = self._ra_make_companion('hold', cmds, -ret_bal, name)
                lines = comp.line_ids.sorted('id')
                for (term, _c, _b), dr_line in zip(pairs, lines):
                    (term + dr_line).reconcile()
                (ret_line + lines[-1]).reconcile()
        if self.ra_recovery_amount and orig._ra_active_companions('advance'):
            name = _("Advance restored")
            pairs = self._ra_split(self.ra_recovery_amount)
            tot_cur = sum(p[1] for p in pairs)
            tot_bal = sum(p[2] for p in pairs)
            cmds = [self._ra_line_cmd(t.account_id, c, b, name, t.date_maturity) for t, c, b in pairs]
            # The restored advance comes back as an open customer credit.
            cmds.append(self._ra_line_cmd(
                pairs[0][0].account_id, -tot_cur, -tot_bal, _("Advance restored %s", self.name), self.date))
            adv = orig._ra_active_companions('advance')[:1].ra_entry_payment_id
            comp = self._ra_make_companion('advance', cmds, -tot_bal, name, advance=adv)
            lines = comp.line_ids.sorted('id')
            for (term, _c, _b), dr_line in zip(pairs, lines):
                (term + dr_line).reconcile()

    # ------------------------------------------------------------------
    # Reset to draft / reversal
    # ------------------------------------------------------------------
    def _ra_check_reversible(self):
        self.ensure_one()
        source = self.ra_source_move_id
        if self.ra_kind == 'hold' and source.move_type == 'out_invoice':
            account = self.company_id.ra_retention_account_id
            ret_line = self.line_ids.filtered(lambda l: l.account_id == account)
            if ret_line.matched_credit_ids or ret_line.matched_debit_ids:
                raise UserError(_(
                    "The retention of %s was already released or reduced by a credit note. "
                    "Reverse those entries first.", source.display_name))

    def _ra_reverse_one(self):
        self.ensure_one()
        rev_date = max(fields.Date.context_today(self), self.date)
        if self.company_id._get_violated_lock_dates(rev_date, False, self.journal_id):
            raise UserError(_("The reversal date %s is in a locked period.", rev_date))
        self.line_ids.remove_move_reconcile()
        rev = self._reverse_moves(
            [{'date': rev_date, 'ref': _("Reversal of: %s", self.name)}], cancel=False)
        rev._post(soft=False)
        groups = (self.line_ids + rev.line_ids).grouped(lambda l: (l.account_id, l.currency_id))
        for (account, _cur), lines in groups.items():
            if account.reconcile:
                lines.reconcile()

    def _ra_reverse_companions(self):
        for move in self:
            comps = move._ra_active_companions()
            for comp in comps:
                comp._ra_check_reversible()
            for comp in comps:
                comp._ra_reverse_one()

    def button_draft(self):
        self.filtered(lambda m: m.move_type in SALE_TYPES and m.state == 'posted')._ra_reverse_companions()
        return super().button_draft()

    def _reverse_moves(self, default_values_list=None, cancel=False):
        if cancel:
            # Full cancellation of an invoice: undo the retention entries first so that the
            # standard reconciliation of invoice vs credit note nets out to zero.
            self.filtered(lambda m: m.move_type in SALE_TYPES and m.ra_companion_ids)._ra_reverse_companions()
            self = self.with_context(ra_skip_companions=True)  # noqa: PLW0642
        reversed_moves = super()._reverse_moves(default_values_list, cancel=cancel)
        for orig, rev in zip(self, reversed_moves):
            if not orig.ra_kind:
                continue
            rev.write({
                'ra_kind': orig.ra_kind,
                'ra_source_move_id': orig.ra_source_move_id.id,
                'ra_signed_amount': -orig.ra_signed_amount,
                'ra_partner_id': orig.ra_partner_id.id,
                'ra_project_id': orig.ra_project_id.id,
                'ra_entry_payment_id': orig.ra_entry_payment_id.id,
                'ra_reversed': True,
            })
            orig.ra_reversed = True
        return reversed_moves
