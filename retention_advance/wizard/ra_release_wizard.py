from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError


class RaReleaseWizard(models.TransientModel):
    _name = 'ra.release.wizard'
    _description = 'Release Retention'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, domain="[('parent_id', '=', False)]")
    date = fields.Date(string='Release Date', required=True, default=fields.Date.context_today)
    line_ids = fields.One2many('ra.release.wizard.line', 'wizard_id', string='Retentions')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'res.partner' and self.env.context.get('active_id'):
            partner = self.env['res.partner'].browse(self.env.context['active_id'])
            res['partner_id'] = partner.commercial_partner_id.id
        return res

    @api.onchange('partner_id', 'company_id')
    def _onchange_partner_id(self):
        self.line_ids = [Command.clear()]
        if not self.partner_id:
            return
        account = self.company_id.ra_retention_account_id
        if not account:
            return
        lines = self.env['account.move.line'].search([
            ('company_id', '=', self.company_id.id),
            ('account_id', '=', account.id),
            ('parent_state', '=', 'posted'),
            ('reconciled', '=', False),
            ('debit', '>', 0),
            ('move_id.ra_kind', '=', 'hold'),
            ('move_id.ra_partner_id', '=', self.partner_id.commercial_partner_id.id),
        ], order='date_maturity, id')
        self.line_ids = [Command.create({'retention_line_id': l.id}) for l in lines
                         if l.amount_residual_currency > 0]

    def action_fill_full(self):
        self.ensure_one()
        for line in self.line_ids:
            line.release_amount = line.balance
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
            'view_mode': 'form', 'target': 'new',
        }

    def action_release(self):
        self.ensure_one()
        company = self.company_id
        if company not in self.env.companies:
            raise UserError(_("Select company %s in the company switcher first.", company.name))
        lines = self.line_ids.filtered(lambda l: l.release_amount)
        if not lines:
            raise UserError(_("Enter an amount to release on at least one line."))
        journal = company._ra_get_journal()
        if company._get_violated_lock_dates(self.date, False, journal):
            raise UserError(_("The release date %s is in a locked period.", self.date))
        for line in lines:
            cur = line.currency_id
            if cur.compare_amounts(line.release_amount, 0.0) < 0:
                raise UserError(_("Amounts to release cannot be negative."))
            if cur.compare_amounts(line.release_amount, line.balance) > 0:
                raise UserError(_(
                    "You cannot release %(asked)s of %(inv)s: only %(left)s is held.",
                    asked=cur.format(line.release_amount), inv=line.invoice_id.display_name,
                    left=cur.format(line.balance)))

        # one release entry per source invoice (keeps the project traceable)
        for invoice, inv_lines in lines.grouped('invoice_id').items():
            cmds, pairs, total_bal = [], [], 0.0
            for wl in inv_lines:
                ret = wl.retention_line_id
                amount = wl.release_amount
                if wl.currency_id.is_zero(amount - ret.amount_residual_currency):
                    bal = ret.amount_residual
                else:
                    bal = company.currency_id.round(
                        amount * ret.amount_residual / ret.amount_residual_currency)
                term = invoice.line_ids.filtered(lambda l: l.display_type == 'payment_term')[:1]
                receivable = term.account_id or self.partner_id.with_company(company).property_account_receivable_id
                label = _("Retention release %s", invoice.name)
                base = {'partner_id': self.partner_id.id, 'currency_id': ret.currency_id.id, 'name': label}
                cmds.append(Command.create({
                    **base, 'account_id': ret.account_id.id, 'amount_currency': -amount, 'balance': -bal}))
                cmds.append(Command.create({
                    **base, 'account_id': receivable.id, 'amount_currency': amount, 'balance': bal,
                    'date_maturity': self.date}))
                pairs.append(ret)
                total_bal += bal
            move = self.env['account.move'].with_company(company).create({
                'move_type': 'entry', 'journal_id': journal.id, 'company_id': company.id,
                'date': self.date, 'ref': _("Retention release - %s", invoice.name),
                'ra_kind': 'release', 'ra_source_move_id': invoice.id, 'ra_signed_amount': total_bal,
                'ra_partner_id': self.partner_id.id, 'ra_project_id': invoice.ra_project_id.id,
                'line_ids': cmds,
            })
            move._post(soft=False)
            new_lines = move.line_ids.sorted('id')
            for idx, ret in enumerate(pairs):
                (ret + new_lines[idx * 2]).reconcile()
        return {'type': 'ir.actions.act_window_close'}


class RaReleaseWizardLine(models.TransientModel):
    _name = 'ra.release.wizard.line'
    _description = 'Release Retention Line'

    wizard_id = fields.Many2one('ra.release.wizard', required=True, ondelete='cascade')
    retention_line_id = fields.Many2one('account.move.line', required=True)
    invoice_id = fields.Many2one(
        'account.move', string='Invoice', compute='_compute_info', store=True)
    project_id = fields.Many2one('sale.order', string='Project', compute='_compute_info', store=True)
    due_date = fields.Date(string='Expected Handover', compute='_compute_info', store=True)
    currency_id = fields.Many2one('res.currency', compute='_compute_info', store=True)
    balance = fields.Monetary(
        string='Retention Held', currency_field='currency_id', compute='_compute_info', store=True)
    release_amount = fields.Monetary(string='Amount to Release', currency_field='currency_id')

    @api.depends('retention_line_id')
    def _compute_info(self):
        for line in self:
            ret = line.retention_line_id
            line.invoice_id = ret.move_id.ra_source_move_id
            line.project_id = ret.move_id.ra_project_id
            line.due_date = ret.date_maturity
            line.currency_id = ret.currency_id
            line.balance = ret.amount_residual_currency
