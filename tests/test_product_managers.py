# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import RanchiCommon


@tagged('post_install', '-at_install', 'ranchi')
class TestProductManagers(RanchiCommon):
    """Ranchi (weekly) and Rapid (daily) collections are run by different managers:
    each one only sees and handles the loan types that list them as manager."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.rapid_type = cls.env['ranchi.loan.type'].create({
            'name': 'Rapid Daily', 'service_rate': 8.0, 'installment_count': 30,
            'installment_period': 'daily', 'meeting_frequency': 'daily'})
        cls.rapid_union = cls.env['ranchi.union'].create({
            'name': 'Rapid Union', 'company_id': cls.company.id,
            'loan_type_id': cls.rapid_type.id, 'credit_officer_id': cls.officer.id})
        cls.rapid_member = cls.env['res.partner'].create({
            'name': 'Rapid Member', 'is_ranchi_member': True, 'union_id': cls.rapid_union.id,
            'company_id': cls.company.id, 'nin': '99988877766'})

        def user(login, *groups):
            return cls.env['res.users'].create({
                'name': login, 'login': login,
                'company_id': cls.company.id, 'company_ids': [(4, cls.company.id)],
                'groups_id': [(4, cls.env.ref(g).id) for g in groups],
            })
        cls.ranchi_manager = user('ranchi_mgr', 'ranchi_centre.group_ranchi_manager')
        cls.rapid_manager = user('rapid_mgr', 'ranchi_centre.group_ranchi_manager')
        cls.general_manager = user('general_mgr', 'ranchi_centre.group_ranchi_general_manager')
        cls.accountant = user('accountant', 'ranchi_centre.group_ranchi_accountant')
        cls.auditor = user('auditor', 'ranchi_centre.group_ranchi_auditor')
        cls.loan_type.manager_ids = cls.ranchi_manager
        cls.rapid_type.manager_ids = cls.rapid_manager

        cls.ranchi_loan = cls.env['ranchi.loan'].create({
            'member_id': cls.member.id, 'company_id': cls.company.id,
            'loan_type_id': cls.loan_type.id, 'amount_applied': 10000.0})
        cls.rapid_loan = cls.env['ranchi.loan'].create({
            'member_id': cls.rapid_member.id, 'company_id': cls.company.id,
            'loan_type_id': cls.rapid_type.id, 'amount_applied': 10000.0})
        Collection = cls.env['ranchi.collection']
        cls.ranchi_col = Collection.create({
            'union_id': cls.union.id, 'company_id': cls.company.id, 'journal_id': cls.bank_journal.id})
        cls.rapid_col = Collection.create({
            'union_id': cls.rapid_union.id, 'company_id': cls.company.id, 'journal_id': cls.bank_journal.id})

    def _visible(self, user, model, records):
        return self.env[model].with_user(user).search([('id', 'in', records.ids)])

    def test_collection_carries_loan_type(self):
        self.assertEqual(self.ranchi_col.loan_type_id, self.loan_type)
        self.assertEqual(self.rapid_col.loan_type_id, self.rapid_type)

    def test_each_manager_sees_only_own_product(self):
        unions = self.union | self.rapid_union
        loans = self.ranchi_loan | self.rapid_loan
        cols = self.ranchi_col | self.rapid_col
        members = self.member | self.rapid_member
        for mgr, union, loan, col, member in (
                (self.ranchi_manager, self.union, self.ranchi_loan, self.ranchi_col, self.member),
                (self.rapid_manager, self.rapid_union, self.rapid_loan, self.rapid_col, self.rapid_member)):
            self.assertEqual(self._visible(mgr, 'ranchi.union', unions), union)
            self.assertEqual(self._visible(mgr, 'ranchi.loan', loans), loan)
            self.assertEqual(self._visible(mgr, 'ranchi.collection', cols), col)
            self.assertEqual(self._visible(mgr, 'res.partner', members), member)

    def test_other_product_collection_is_unreadable(self):
        with self.assertRaises(AccessError):
            self.rapid_col.with_user(self.ranchi_manager).read(['amount_total'])
        with self.assertRaises(AccessError):
            self.ranchi_col.with_user(self.rapid_manager).read(['amount_total'])

    def test_cash_handling_limited_to_own_product(self):
        self.ranchi_col.with_user(self.ranchi_manager)._check_manager()
        with self.assertRaises(AccessError):
            self.rapid_col.with_user(self.ranchi_manager)._check_manager()
        # the general manager handles both
        (self.ranchi_col | self.rapid_col).with_user(self.general_manager)._check_manager()

    def test_branch_staff_see_both_products(self):
        unions = self.union | self.rapid_union
        cols = self.ranchi_col | self.rapid_col
        members = self.member | self.rapid_member
        for user in (self.general_manager, self.accountant, self.auditor):
            self.assertEqual(self._visible(user, 'ranchi.union', unions), unions)
            self.assertEqual(self._visible(user, 'ranchi.collection', cols), cols)
            self.assertEqual(self._visible(user, 'res.partner', members), members)

    def test_manager_of_both_products_sees_both(self):
        self.rapid_type.manager_ids |= self.ranchi_manager
        cols = self.ranchi_col | self.rapid_col
        self.assertEqual(self._visible(self.ranchi_manager, 'ranchi.collection', cols), cols)
        cols.with_user(self.ranchi_manager)._check_manager()

    def test_product_manager_cannot_edit_loan_types(self):
        with self.assertRaises(AccessError):
            self.rapid_type.with_user(self.ranchi_manager).write({'manager_ids': [(4, self.ranchi_manager.id)]})
