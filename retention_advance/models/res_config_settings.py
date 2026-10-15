from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ra_retention_account_id = fields.Many2one(
        related='company_id.ra_retention_account_id', readonly=False)
    ra_default_retention_percent = fields.Float(
        related='company_id.ra_default_retention_percent', readonly=False)
    ra_journal_id = fields.Many2one(
        related='company_id.ra_journal_id', readonly=False)
