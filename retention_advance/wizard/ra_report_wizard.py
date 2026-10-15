import base64
import io
from collections import defaultdict

from odoo import Command, api, fields, models, _
from odoo.exceptions import UserError

REPORT_COLUMNS = [
    ('total_untaxed', 'Work Done (excl. VAT)'),
    ('total_invoiced', 'Total Invoiced (incl. VAT)'),
    ('retention_held', 'Retention Held'),
    ('retention_released', 'Retention Released'),
    ('retention_balance', 'Retention Balance'),
    ('advance_received', 'Advance Received'),
    ('advance_recovered', 'Advance Recovered'),
    ('advance_balance', 'Advance Balance'),
]


class RaReportWizard(models.TransientModel):
    _name = 'ra.report.wizard'
    _description = 'Retention & Advance Report'

    date_to = fields.Date(string='As At', required=True, default=fields.Date.context_today)
    company_ids = fields.Many2many(
        'res.company', string='Companies', required=True,
        default=lambda self: self.env.companies)
    partner_ids = fields.Many2many('res.partner', string='Customers',
                                   domain="[('parent_id', '=', False)]")
    line_ids = fields.One2many('ra.report.line', 'wizard_id', string='Lines')

    # ------------------------------------------------------------------
    def _compute_data(self):
        self.ensure_one()
        companies = self.company_ids & self.env.companies
        if not companies:
            raise UserError(_("Select at least one of your allowed companies."))
        partners = self.partner_ids.commercial_partner_id
        Move = self.env['account.move']
        data = defaultdict(lambda: defaultdict(float))

        def key(company, partner, project):
            return (company.id, partner.id, project.id)

        base = [('company_id', 'in', companies.ids), ('date', '<=', self.date_to), ('state', '=', 'posted')]
        inv_domain = base + [('move_type', 'in', ('out_invoice', 'out_refund'))]
        if partners:
            inv_domain.append(('commercial_partner_id', 'in', partners.ids))
        for company, partner, project, untaxed, total in Move._read_group(
                inv_domain, ['company_id', 'commercial_partner_id', 'ra_project_id'],
                ['amount_untaxed_signed:sum', 'amount_total_signed:sum']):
            d = data[key(company, partner, project)]
            d['total_untaxed'] += untaxed
            d['total_invoiced'] += total

        ra_domain = base + [('ra_kind', '!=', False)]
        if partners:
            ra_domain.append(('ra_partner_id', 'in', partners.ids))
        for company, partner, project, kind, amount in Move._read_group(
                ra_domain, ['company_id', 'ra_partner_id', 'ra_project_id', 'ra_kind'],
                ['ra_signed_amount:sum']):
            d = data[key(company, partner, project)]
            if kind == 'hold':
                d['retention_held'] += amount
            elif kind == 'release':
                d['retention_released'] += amount
            else:
                d['advance_recovered'] += amount

        pay_domain = [
            ('company_id', 'in', companies.ids), ('date', '<=', self.date_to),
            ('ra_is_advance', '=', True), ('payment_type', '=', 'inbound'),
            ('state', 'in', ('in_process', 'paid')),
        ]
        for payment in self.env['account.payment'].search(pay_domain):
            if partners and payment.partner_id.commercial_partner_id not in partners:
                continue
            d = data[key(payment.company_id, payment.partner_id.commercial_partner_id, payment.ra_project_id)]
            d['advance_received'] += payment.amount_company_currency_signed

        rows = []
        for (company_id, partner_id, project_id), d in data.items():
            d['retention_balance'] = d['retention_held'] - d['retention_released']
            d['advance_balance'] = d['advance_received'] - d['advance_recovered']
            rows.append({
                'company_id': company_id, 'partner_id': partner_id, 'project_id': project_id or False,
                **{k: d[k] for k, _label in REPORT_COLUMNS},
            })
        rows.sort(key=lambda r: (r['company_id'], r['partner_id'], r['project_id'] or 0))
        return rows

    def action_generate(self):
        self.ensure_one()
        rows = self._compute_data()
        self.line_ids = [Command.clear()] + [Command.create(r) for r in rows]
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
            'view_mode': 'form', 'target': 'current',
        }

    def action_export_xlsx(self):
        self.ensure_one()
        import xlsxwriter
        rows = self._compute_data()
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet(_("Retention & Advance")[:31])
        head = wb.add_format({'bold': True, 'bg_color': '#D9E1F2', 'border': 1, 'text_wrap': True, 'align': 'center'})
        title = wb.add_format({'bold': True, 'font_size': 14})
        num = wb.add_format({'num_format': '#,##0.00'})
        bold_num = wb.add_format({'num_format': '#,##0.00', 'bold': True, 'top': 1})
        bold = wb.add_format({'bold': True, 'top': 1})
        ws.write(0, 0, _("Retention & Advance Report"), title)
        ws.write(1, 0, _("As at %s (amounts in company currency)", self.date_to))
        headers = [_("Company"), _("Customer"), _("Project")] + [_(label) for _k, label in REPORT_COLUMNS]
        for col, h in enumerate(headers):
            ws.write(3, col, h, head)
        companies = {c.id: c for c in self.env['res.company'].browse({r['company_id'] for r in rows})}
        partners = {p.id: p for p in self.env['res.partner'].browse({r['partner_id'] for r in rows})}
        projects = {p.id: p for p in self.env['sale.order'].browse({r['project_id'] for r in rows if r['project_id']})}
        totals = defaultdict(float)
        for i, r in enumerate(rows, start=4):
            ws.write(i, 0, companies[r['company_id']].name)
            ws.write(i, 1, partners[r['partner_id']].name or '')
            ws.write(i, 2, projects[r['project_id']].name if r['project_id'] else _("(no project)"))
            for j, (k, _label) in enumerate(REPORT_COLUMNS, start=3):
                ws.write_number(i, j, r[k], num)
                totals[k] += r[k]
        last = 4 + len(rows)
        if rows:
            ws.write(last, 0, _("Total"), bold)
            ws.write(last, 1, '', bold)
            ws.write(last, 2, '', bold)
            if len(companies) > 1:
                ws.write(last + 1, 0, _("Totals mix company currencies."))
            for j, (k, _label) in enumerate(REPORT_COLUMNS, start=3):
                ws.write_number(last, j, totals[k], bold_num)
        ws.set_column(0, 1, 28)
        ws.set_column(2, 2, 22)
        ws.set_column(3, 10, 18)
        ws.freeze_panes(4, 3)
        wb.close()
        attachment = self.env['ir.attachment'].create({
            'name': 'retention_advance_%s.xlsx' % self.date_to,
            'datas': base64.b64encode(output.getvalue()),
            'res_model': self._name, 'res_id': self.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        return {'type': 'ir.actions.act_url', 'url': '/web/content/%s?download=true' % attachment.id,
                'target': 'self'}


class RaReportLine(models.TransientModel):
    _name = 'ra.report.line'
    _description = 'Retention & Advance Report Line'
    _order = 'company_id, partner_id, project_id'

    wizard_id = fields.Many2one('ra.report.wizard', required=True, ondelete='cascade')
    company_id = fields.Many2one('res.company', readonly=True)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id')
    partner_id = fields.Many2one('res.partner', string='Customer', readonly=True)
    project_id = fields.Many2one('sale.order', string='Project', readonly=True)
    total_untaxed = fields.Monetary(string='Work Done (excl. VAT)')
    total_invoiced = fields.Monetary(string='Total Invoiced')
    retention_held = fields.Monetary()
    retention_released = fields.Monetary()
    retention_balance = fields.Monetary()
    advance_received = fields.Monetary()
    advance_recovered = fields.Monetary()
    advance_balance = fields.Monetary()
