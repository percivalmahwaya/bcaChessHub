"""
Tests for the payments app.

WHY THIS FILE EXISTS. Every other app here had tests; payments had none — and
payments is the only app that moves money and grants tournament entry. Writing
them surfaced two ways to get a paid tournament place without paying, both live
on the public site at the time:

  1. `paynow_callback` verified no hash. It is unauthenticated and CSRF-exempt
     by necessity, so the hash was the only control, and it was not checked.
     `POST reference=x-<id>&status=paid` completed any payment. Payment ids are
     sequential integers.

  2. `PAYNOW_SANDBOX` defaulted to True and the Railway variable was never set,
     so `sandbox_approve` — which marks a payment paid for free, by design —
     was reachable in production, with no login and no ownership check.

Most of what follows is therefore written as an attack, not as a feature test.
A test that only confirmed the happy path would have passed against both bugs.
"""

import datetime
from pathlib import Path
import hashlib

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from associations.models import Association
from members.models import Member
from tournaments.models import Tournament, TournamentRegistration

from .models import Payment
from . import paynow_client


def make_assoc():
    return Association.objects.create(
        name='Bulawayo Chess', city='Bulawayo', email='bca@chess.zw')


def make_member(username, assoc, password='pass1234'):
    user = User.objects.create_user(username=username, password=password)
    return Member.objects.create(user=user, association=assoc, rating=1200)


def make_tournament(assoc, fee='10.00'):
    today = datetime.date.today()
    return Tournament.objects.create(
        association=assoc, name='Open', status='upcoming',
        start_date=today, end_date=today, location='Bulawayo',
        num_rounds=5, registration_fee=fee, currency='USD',
    )


def make_payment(member, tournament, status='awaiting_approval'):
    return Payment.objects.create(
        member=member, tournament=tournament,
        amount=tournament.registration_fee, currency='USD',
        gateway='ecocash', status=status,
    )


def signed_callback(payment, status='Paid', key=None):
    """A callback POST body carrying a VALID Paynow hash."""
    key = key if key is not None else paynow_client.PAYNOW_INTEGRATION_KEY
    data = {
        'reference': f'CHESSHUB-{payment.pk}',
        'paynowreference': '1234567',
        'amount': str(payment.amount),
        'status': status,
        'pollurl': 'https://paynow.co.zw/poll/abc',
    }
    concat = ''.join(data[k] for k in paynow_client.CALLBACK_FIELD_ORDER) + key
    data['hash'] = hashlib.sha512(concat.encode('utf-8')).hexdigest().upper()
    return data


# ---------------------------------------------------------------- model

class PaymentModelTest(TestCase):
    def setUp(self):
        self.assoc = make_assoc()
        self.member = make_member('alice', self.assoc)
        self.tournament = make_tournament(self.assoc)

    def test_mark_completed_sets_status_and_timestamp(self):
        p = make_payment(self.member, self.tournament)
        self.assertIsNone(p.completed_at)
        p.mark_completed(gateway_reference='REF123')
        p.refresh_from_db()
        self.assertEqual(p.status, 'completed')
        self.assertEqual(p.gateway_reference, 'REF123')
        self.assertIsNotNone(p.completed_at)

    def test_completing_payment_confirms_pending_registration(self):
        reg = TournamentRegistration.objects.create(
            tournament=self.tournament, player=self.member, status='pending')
        make_payment(self.member, self.tournament).mark_completed()
        reg.refresh_from_db()
        self.assertEqual(reg.status, 'confirmed')

    def test_completing_payment_does_not_touch_another_players_registration(self):
        bob = make_member('bob', self.assoc)
        other = TournamentRegistration.objects.create(
            tournament=self.tournament, player=bob, status='pending')
        make_payment(self.member, self.tournament).mark_completed()
        other.refresh_from_db()
        self.assertEqual(other.status, 'pending')

    def test_payment_without_tournament_completes_harmlessly(self):
        p = Payment.objects.create(
            member=self.member, tournament=None, amount='5.00',
            currency='USD', gateway='cash', status='pending')
        p.mark_completed()
        self.assertEqual(p.status, 'completed')


# ------------------------------------------------- callback hash checks

class CallbackHashTest(TestCase):
    def setUp(self):
        self.assoc = make_assoc()
        self.member = make_member('alice', self.assoc)
        self.tournament = make_tournament(self.assoc)
        self.payment = make_payment(self.member, self.tournament)
        self.url = reverse('paynow_callback')

    # --- the attack the old code allowed -------------------------------

    def test_forged_callback_without_hash_is_rejected(self):
        """The exact request that used to work. It must not."""
        resp = self.client.post(self.url, {
            'reference': f'CHESSHUB-{self.payment.pk}',
            'status': 'Paid',
        })
        self.assertEqual(resp.status_code, 403)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'awaiting_approval')

    def test_forged_callback_with_wrong_hash_is_rejected(self):
        data = signed_callback(self.payment)
        data['hash'] = 'F' * 128
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 403)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'awaiting_approval')

    def test_hash_computed_with_the_wrong_integration_key_is_rejected(self):
        """Someone who knows the scheme but not the secret still fails."""
        data = signed_callback(self.payment, key='NOT-THE-REAL-KEY')
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 403)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'awaiting_approval')

    def test_tampering_with_amount_after_signing_is_rejected(self):
        data = signed_callback(self.payment)
        data['amount'] = '0.01'
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 403)

    def test_forged_callback_does_not_confirm_registration(self):
        reg = TournamentRegistration.objects.create(
            tournament=self.tournament, player=self.member, status='pending')
        self.client.post(self.url, {
            'reference': f'CHESSHUB-{self.payment.pk}', 'status': 'Paid'})
        reg.refresh_from_db()
        self.assertEqual(reg.status, 'pending')

    # --- the legitimate path still has to work -------------------------

    def test_correctly_signed_paid_callback_completes_the_payment(self):
        resp = self.client.post(self.url, signed_callback(self.payment))
        self.assertEqual(resp.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'completed')

    def test_correctly_signed_callback_confirms_registration(self):
        reg = TournamentRegistration.objects.create(
            tournament=self.tournament, player=self.member, status='pending')
        self.client.post(self.url, signed_callback(self.payment))
        reg.refresh_from_db()
        self.assertEqual(reg.status, 'confirmed')

    def test_signed_cancelled_callback_marks_cancelled_not_completed(self):
        self.client.post(self.url, signed_callback(self.payment, status='Cancelled'))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, 'cancelled')

    def test_signed_callback_for_unknown_payment_returns_400(self):
        data = {
            'reference': 'CHESSHUB-999999',
            'paynowreference': '1234567',
            'amount': '10.00',
            'status': 'Paid',
            'pollurl': 'https://paynow.co.zw/poll/abc',
        }
        concat = (''.join(data[k] for k in paynow_client.CALLBACK_FIELD_ORDER)
                  + paynow_client.PAYNOW_INTEGRATION_KEY)
        data['hash'] = hashlib.sha512(concat.encode()).hexdigest().upper()
        resp = self.client.post(self.url, data)
        self.assertEqual(resp.status_code, 400)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_verify_helper_rejects_empty_payload(self):
        self.assertFalse(paynow_client.verify_callback_hash({}))


# -------------------------------------------------- sandbox exposure

class SandboxExposureTest(TestCase):
    """
    The sandbox views mark payments paid without payment. That is the whole
    point of them in development, and exactly why production must not serve
    them. `PAYNOW_SANDBOX` defaulting to True meant it did.
    """

    def setUp(self):
        self.assoc = make_assoc()
        self.member = make_member('alice', self.assoc)
        self.other = make_member('mallory', self.assoc)
        self.tournament = make_tournament(self.assoc)
        self.payment = make_payment(self.member, self.tournament)

    @override_settings(DEBUG=False)
    def test_sandbox_approve_is_refused_in_production(self):
        self.client.login(username='alice', password='pass1234')
        resp = self.client.post(
            reverse('sandbox_approve', args=[self.payment.pk]))
        self.assertEqual(resp.status_code, 403)
        self.payment.refresh_from_db()
        self.assertNotEqual(self.payment.status, 'completed')

    @override_settings(DEBUG=False)
    def test_sandbox_checkout_is_refused_in_production(self):
        resp = self.client.get(
            reverse('sandbox_checkout', args=[self.payment.pk]))
        self.assertEqual(resp.status_code, 403)

    @override_settings(DEBUG=False)
    def test_sandbox_poll_is_refused_in_production(self):
        resp = self.client.get(reverse('sandbox_poll', args=[self.payment.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_sandbox_approve_requires_login(self):
        resp = self.client.post(
            reverse('sandbox_approve', args=[self.payment.pk]))
        self.assertIn(resp.status_code, (302, 403))
        self.payment.refresh_from_db()
        self.assertNotEqual(self.payment.status, 'completed')

    def test_sandbox_approve_refuses_somebody_elses_payment(self):
        self.client.login(username='mallory', password='pass1234')
        resp = self.client.post(
            reverse('sandbox_approve', args=[self.payment.pk]))
        self.assertIn(resp.status_code, (403, 404))
        self.payment.refresh_from_db()
        self.assertNotEqual(self.payment.status, 'completed')

    def test_sandbox_default_is_not_hardcoded_true(self):
        """
        Guard the DEFAULT, not the runtime value.

        The runtime value cannot be asserted here: Django's test runner forces
        DEBUG=False, but PAYNOW_SANDBOX was already resolved at import time
        from the developer's real settings, so it is legitimately True in a
        local test run. Asserting on it produced a failing test that was
        describing the test environment rather than the bug.

        What actually needs protecting is the default in settings.py. It was
        `default=True`, which is how production ended up serving the sandbox
        approval endpoint. This fails if anyone changes it back.
        """
        source = (Path(settings.BASE_DIR) / 'config' / 'settings.py').read_text(
            encoding='utf-8')
        self.assertIn("config('PAYNOW_SANDBOX', default=DEBUG", source,
                      'PAYNOW_SANDBOX must default to DEBUG, never to True')
        self.assertNotIn("config('PAYNOW_SANDBOX', default=True", source)

    def test_production_guard_refuses_sandbox_in_production(self):
        """The settings guard itself: DEBUG off + sandbox on must not boot."""
        source = (Path(settings.BASE_DIR) / 'config' / 'settings.py').read_text(
            encoding='utf-8')
        self.assertIn('if not DEBUG and PAYNOW_SANDBOX:', source)
        self.assertIn('ImproperlyConfigured', source)


# ------------------------------------------------------- payment flow

class InitiatePaymentTest(TestCase):
    def setUp(self):
        self.assoc = make_assoc()
        self.member = make_member('alice', self.assoc)
        self.tournament = make_tournament(self.assoc, fee='10.00')
        self.url = reverse('payment_initiate', args=[self.tournament.pk])

    def test_anonymous_user_is_redirected_to_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_free_tournament_needs_no_payment(self):
        free = make_tournament(self.assoc, fee='0.00')
        self.client.login(username='alice', password='pass1234')
        resp = self.client.get(reverse('payment_initiate', args=[free.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Payment.objects.filter(tournament=free).count(), 0)

    def test_already_paid_does_not_create_a_second_payment(self):
        make_payment(self.member, self.tournament, status='completed')
        self.client.login(username='alice', password='pass1234')
        self.client.get(self.url)
        self.assertEqual(
            Payment.objects.filter(member=self.member,
                                   tournament=self.tournament).count(), 1)

    def test_mobile_money_requires_a_phone_number(self):
        self.client.login(username='alice', password='pass1234')
        resp = self.client.post(self.url, {'gateway': 'ecocash', 'phone_number': ''})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Payment.objects.count(), 0)


class PollPaymentTest(TestCase):
    def setUp(self):
        self.assoc = make_assoc()
        self.member = make_member('alice', self.assoc)
        self.other = make_member('mallory', self.assoc)
        self.tournament = make_tournament(self.assoc)
        self.payment = make_payment(self.member, self.tournament)

    def test_polling_requires_login(self):
        resp = self.client.get(reverse('poll_payment', args=[self.payment.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_cannot_poll_somebody_elses_payment(self):
        self.client.login(username='mallory', password='pass1234')
        resp = self.client.get(reverse('poll_payment', args=[self.payment.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_completed_payment_reports_completed(self):
        self.payment.status = 'completed'
        self.payment.save(update_fields=['status'])
        self.client.login(username='alice', password='pass1234')
        resp = self.client.get(reverse('poll_payment', args=[self.payment.pk]))
        self.assertEqual(resp.json()['status'], 'completed')
