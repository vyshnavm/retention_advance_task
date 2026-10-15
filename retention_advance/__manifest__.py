{
    'name': 'Contractor Retention & Advance Recovery',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Retention holdback and advance recovery on progress invoices',
    'description': """
Progress invoicing for contractors
==================================
* Retention held back on each customer invoice (own account, own due date)
* Automatic recovery of an advance payment against each invoice
* Release Retention wizard, credit-note proportional handling
* Per customer / project report with Excel export
""",
    'author': 'Vyshnav',
    'license': 'LGPL-3',
    'depends': ['account', 'sale'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/account_move_views.xml',
        'views/account_payment_views.xml',
        'wizard/ra_release_wizard_views.xml',
        'wizard/ra_report_wizard_views.xml',
        'views/menus.xml',
        'report/account_report_invoice.xml',
    ],
    'installable': True,
    'application': False,
}
