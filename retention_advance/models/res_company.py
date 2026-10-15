from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    ra_retention_account_id = fields.Many2one(
        'account.account',
        string='Retention Receivable Account',
        domain="[('reconcile', '=', True),"
               " ('account_type', 'not in', ('asset_receivable', 'liability_payable',"
               " 'asset_cash', 'liability_credit_card'))]",
        help="Account that holds retentions until project handover. It must allow reconciliation "
             "and must NOT be of type Receivable, otherwise the invoice residual would include it.",
    )
    ra_default_retention_percent = fields.Float(
        string='Default Retention %', digits=(16, 4),
        help="Proposed on every new customer invoice.",
    )
    ra_journal_id = fields.Many2one(
        'account.journal',
        string='Retention Journal',
        domain="[('type', '=', 'general'), ('company_id', '=', id)]",
        help="Miscellaneous journal used for the retention / advance recovery / release entries. "
             "Defaults to the first miscellaneous journal of the company.",
    )

    @api.constrains('ra_default_retention_percent')
    def _check_ra_default_retention_percent(self):
        for company in self:
            if not 0.0 <= company.ra_default_retention_percent <= 100.0:
                raise ValidationError(_("The default retention % must be between 0 and 100."))

    def _ra_get_journal(self):
        self.ensure_one()
        journal = self.ra_journal_id or self.env['account.journal'].search(
            [('company_id', '=', self.id), ('type', '=', 'general')], limit=1)
        if not journal:
            raise ValidationError(_("No miscellaneous journal found for company %s.", self.name))
        return journal
