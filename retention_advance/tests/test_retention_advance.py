from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged('post_install', '-at_install')
class TestRetentionAdvance(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.retention_account = cls.env['account.account'].create({
            'name': 'Retention Receivable', 'code': '129900',
            'account_type': 'asset_current', 'reconcile': True,
        })
        cls.company.ra_retention_account_id = cls.retention_account
        cls.company.ra_default_retention_percent = 10.0
        cls.partner = cls.partner_a
        cls.tax15 = cls.env['account.tax'].create({
            'name': 'VAT 15', 'amount': 15.0, 'amount_type': 'percent', 'type_tax_use': 'sale'})
        cls.advance = cls._make_advance(200000.0)

    @classmethod
    def _make_advance(cls, amount, currency=None):
        pay = cls.env['account.payment'].create({
            'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': cls.partner.id,
            'amount': amount, 'currency_id': (currency or cls.company.currency_id).id,
        })
        pay.action_post()
        return pay

    def _invoice(self, amount=100000.0, recovery_pct=20.0, post=True, **kw):
        inv = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id,
            'invoice_date': fields.Date.today(),
            'ra_retention_due_date': fields.Date.add(fields.Date.today(), months=12),
            'ra_advance_payment_id': self.advance.id,
            'ra_recovery_percent': recovery_pct,
            'invoice_line_ids': [Command.create({
                'name': 'Work done', 'quantity': 1, 'price_unit': amount,
                'tax_ids': [Command.set(self.tax15.ids)]})],
            **kw,
        })
        if post:
            inv.action_post()
        return inv

    def _retention_balance(self):
        lines = self.env['account.move.line'].search([
            ('account_id', '=', self.retention_account.id), ('parent_state', '=', 'posted'),
            ('partner_id', '=', self.partner.id)])
        return sum(lines.mapped('amount_residual'))

    def test_example_from_brief(self):
        inv = self._invoice()
        self.assertEqual(inv.amount_total, 115000.0)
        self.assertEqual(inv.ra_retention_amount, 10000.0)
        self.assertEqual(inv.ra_recovery_amount, 20000.0)
        self.assertEqual(inv.ra_amount_to_pay, 85000.0)
        self.assertEqual(inv.amount_residual, 85000.0)         # due now
        self.assertEqual(self._retention_balance(), 10000.0)   # separate account
        adv_line = self.advance.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        self.assertEqual(adv_line.amount_residual, -180000.0)  # advance balance

    def test_cannot_recover_more_than_advance(self):
        with self.assertRaises(ValidationError):
            self._invoice(amount=2000000.0, recovery_pct=20.0, post=False)

    def test_reset_to_draft_and_repost_no_duplicates(self):
        inv = self._invoice()
        inv.button_draft()
        self.assertEqual(self._retention_balance(), 0.0)
        adv_line = self.advance.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        self.assertEqual(adv_line.amount_residual, -200000.0)
        inv.action_post()
        self.assertEqual(self._retention_balance(), 10000.0)
        self.assertEqual(adv_line.amount_residual, -180000.0)
        self.assertEqual(inv.amount_residual, 85000.0)
        self.assertEqual(len(inv._ra_active_companions()), 2)

    def test_release_retention_part_then_full(self):
        inv = self._invoice()
        wiz = self.env['ra.release.wizard'].create({
            'partner_id': self.partner.id, 'date': fields.Date.today()})
        wiz._onchange_partner_id()
        self.assertEqual(len(wiz.line_ids), 1)
        wiz.line_ids.release_amount = 4000.0
        wiz.action_release()
        self.assertEqual(self._retention_balance(), 6000.0)
        wiz2 = self.env['ra.release.wizard'].create({'partner_id': self.partner.id})
        wiz2._onchange_partner_id()
        wiz2.action_fill_full()
        wiz2.action_release()
        self.assertEqual(self._retention_balance(), 0.0)
        with self.assertRaises(UserError):
            inv.button_draft()  # retention already released

    def test_partial_credit_note_is_proportional(self):
        inv = self._invoice()
        reversal = self.env['account.move.reversal'].with_context(
            active_model='account.move', active_ids=inv.ids).create({
                'journal_id': inv.journal_id.id, 'reason': 'partial'})
        refund = self.env['account.move'].browse(reversal.refund_moves()['res_id'])
        refund.invoice_line_ids.price_unit = 10000.0   # 10% of the work
        self.assertEqual(refund.ra_retention_amount, 1000.0)
        self.assertEqual(refund.ra_recovery_amount, 2000.0)
        refund.action_post()
        self.assertEqual(self._retention_balance(), 9000.0)

    def test_full_cancel_nets_everything(self):
        inv = self._invoice()
        reversal = self.env['account.move.reversal'].with_context(
            active_model='account.move', active_ids=inv.ids).create({
                'journal_id': inv.journal_id.id, 'reason': 'cancel'})
        reversal.modify_moves()
        self.assertEqual(self._retention_balance(), 0.0)
        adv_line = self.advance.move_id.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        self.assertEqual(adv_line.amount_residual, -200000.0)

    def test_report(self):
        self._invoice()
        wiz = self.env['ra.report.wizard'].create({'date_to': fields.Date.today()})
        wiz.action_generate()
        line = wiz.line_ids.filtered(lambda l: l.partner_id == self.partner)
        self.assertEqual(line.retention_held, 10000.0)
        self.assertEqual(line.advance_recovered, 20000.0)
        self.assertEqual(line.advance_balance, 180000.0)
        self.assertTrue(wiz.action_export_xlsx())

    def test_lock_date_respected_on_release(self):
        self._invoice()
        self.company.fiscalyear_lock_date = fields.Date.add(fields.Date.today(), days=1)
        wiz = self.env['ra.release.wizard'].create({'partner_id': self.partner.id})
        wiz._onchange_partner_id()
        wiz.action_fill_full()
        with self.assertRaises(UserError):
            wiz.action_release()
