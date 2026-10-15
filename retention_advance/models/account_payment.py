from odoo import fields, models


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    ra_is_advance = fields.Boolean(
        string='Advance Payment', copy=False, tracking=True,
        help="Customer advance for a project. Set automatically when the payment is used for "
             "an advance recovery.")
    ra_project_id = fields.Many2one(
        'sale.order', string='Project', copy=False, index='btree_not_null',
        domain="[('partner_id', 'child_of', partner_id)]",
        help="Sales order (project) this advance was received for. Used by the retention report.")
