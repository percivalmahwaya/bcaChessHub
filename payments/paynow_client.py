"""
Paynow Zimbabwe integration for ChessHub.

Supports:
  - EcoCash mobile money (primary for BCA members)
  - InnBucks mobile money
  - Web checkout (VISA / Mastercard / PayPal via Paynow portal)

Sandbox mode:
  Set PAYNOW_SANDBOX = True in settings (default in dev).
  No real money moves; simulates the full flow locally.

Going live:
  1. Register at paynow.co.zw as a merchant
  2. Get your Integration ID and Integration Key
  3. Set PAYNOW_INTEGRATION_ID, PAYNOW_INTEGRATION_KEY in .env
  4. Set PAYNOW_SANDBOX = False
  5. Set SITE_BASE_URL to your live domain
"""

from django.conf import settings

PAYNOW_INTEGRATION_ID = getattr(settings, 'PAYNOW_INTEGRATION_ID', 'SANDBOX_ID')
PAYNOW_INTEGRATION_KEY = getattr(settings, 'PAYNOW_INTEGRATION_KEY', 'SANDBOX_KEY')
PAYNOW_SANDBOX = getattr(settings, 'PAYNOW_SANDBOX', True)
BASE_URL = getattr(settings, 'SITE_BASE_URL', 'http://127.0.0.1:8000')

RESULT_URL = f'{BASE_URL}/payments/callback/'
RETURN_URL = f'{BASE_URL}/payments/return/'


def _get_client():
    from paynow import Paynow
    return Paynow(
        PAYNOW_INTEGRATION_ID,
        PAYNOW_INTEGRATION_KEY,
        RESULT_URL,
        RETURN_URL,
    )


class PaynowResult:
    """Normalised result object returned by all initiation functions."""
    def __init__(self, success, poll_url='', redirect_url='', error='', paynow_reference=''):
        self.success = success
        self.poll_url = poll_url
        self.redirect_url = redirect_url
        self.error = error
        self.paynow_reference = paynow_reference

    def __repr__(self):
        return f'<PaynowResult success={self.success} error={self.error!r}>'


def initiate_ecocash(payment_obj):
    """
    Initiate an EcoCash mobile money push for `payment_obj`.

    Returns a PaynowResult. On success, the customer receives a USSD prompt
    on their phone to approve the payment.
    """
    if PAYNOW_SANDBOX:
        return _sandbox_mobile_result(payment_obj)

    try:
        client = _get_client()
        pn_payment = client.create_payment(
            f'ChessHub-{payment_obj.pk}',
            payment_obj.member.user.email,
        )
        pn_payment.add(payment_obj.description or 'Tournament Registration', float(payment_obj.amount))

        response = client.send_mobile(
            pn_payment,
            payment_obj.phone_number,
            'ecocash',
        )

        if response.success:
            return PaynowResult(
                success=True,
                poll_url=response.poll_url,
                paynow_reference=getattr(response, 'paynow_reference', ''),
            )
        return PaynowResult(success=False, error=str(response.errors))

    except Exception as exc:
        return PaynowResult(success=False, error=str(exc))


def initiate_innbucks(payment_obj):
    """InnBucks mobile money push — same flow as EcoCash via Paynow."""
    if PAYNOW_SANDBOX:
        return _sandbox_mobile_result(payment_obj)

    try:
        client = _get_client()
        pn_payment = client.create_payment(
            f'ChessHub-{payment_obj.pk}',
            payment_obj.member.user.email,
        )
        pn_payment.add(payment_obj.description or 'Tournament Registration', float(payment_obj.amount))
        response = client.send_mobile(pn_payment, payment_obj.phone_number, 'innbucks')

        if response.success:
            return PaynowResult(success=True, poll_url=response.poll_url)
        return PaynowResult(success=False, error=str(response.errors))

    except Exception as exc:
        return PaynowResult(success=False, error=str(exc))


def initiate_web_checkout(payment_obj):
    """
    Web checkout (VISA / Mastercard / PayPal through Paynow portal).
    Returns a redirect URL the player should be sent to.
    """
    if PAYNOW_SANDBOX:
        return PaynowResult(
            success=True,
            redirect_url=f'{BASE_URL}/payments/sandbox-checkout/{payment_obj.pk}/',
            poll_url='https://sandbox.paynow.co.zw/poll/fake',
        )

    try:
        client = _get_client()
        pn_payment = client.create_payment(
            f'ChessHub-{payment_obj.pk}',
            payment_obj.member.user.email,
        )
        pn_payment.add(payment_obj.description or 'Tournament Registration', float(payment_obj.amount))
        response = client.send(pn_payment)

        if response.success:
            return PaynowResult(success=True, redirect_url=response.redirect_url, poll_url=response.poll_url)
        return PaynowResult(success=False, error=str(response.errors))

    except Exception as exc:
        return PaynowResult(success=False, error=str(exc))


def check_payment_status(poll_url):
    """
    Poll Paynow for the current status of a payment.
    Returns one of: 'paid', 'awaiting delivery', 'created', 'sent',
                    'cancelled', 'disputed', 'refunded'
    """
    if PAYNOW_SANDBOX:
        return 'paid'  # sandbox always succeeds

    try:
        client = _get_client()
        status = client.check_transaction_status(poll_url)
        return status.status.lower() if hasattr(status, 'status') else 'unknown'
    except Exception:
        return 'unknown'


# ---------------------------------------------------------------------------
# Sandbox helpers (dev only)
# ---------------------------------------------------------------------------

def _sandbox_mobile_result(payment_obj):
    """Return a fake successful result for sandbox testing."""
    fake_poll = f'{BASE_URL}/payments/sandbox-poll/{payment_obj.pk}/'
    return PaynowResult(
        success=True,
        poll_url=fake_poll,
        paynow_reference=f'SANDBOX-{payment_obj.pk}',
    )

# ---------------------------------------------------------------------------
# Callback verification
# ---------------------------------------------------------------------------

def _hash_fields(data, keys=None):
    """
    Paynow's integrity scheme: concatenate the field VALUES in order, append
    the integration key, SHA512, uppercase hex.

    Every field except `hash` itself participates. Paynow documents a field
    order for callbacks; we follow it when all expected keys are present and
    fall back to the order the POST arrived in otherwise, because a merchant
    that rejects a legitimate callback is as broken as one that accepts a
    forged one.
    """
    import hashlib

    if keys is None:
        keys = [k for k in data.keys() if k.lower() != 'hash']
    concat = ''.join(str(data.get(k, '')) for k in keys)
    concat += PAYNOW_INTEGRATION_KEY
    return hashlib.sha512(concat.encode('utf-8')).hexdigest().upper()


# The order Paynow documents for status-update callbacks.
CALLBACK_FIELD_ORDER = [
    'reference', 'paynowreference', 'amount', 'status', 'pollurl',
]


def verify_callback_hash(data):
    """
    Is this POST genuinely from Paynow?

    WHY THIS MATTERS MORE THAN ANYTHING ELSE IN THIS FILE. The callback
    endpoint is unauthenticated and CSRF-exempt by necessity — Paynow's
    servers call it, not a browser with a session. Without verifying the hash,
    ANY person on the internet can POST

        reference=x-<payment_id>&status=paid

    and the site will mark that payment completed and auto-confirm the
    tournament registration attached to it. Payment ids are sequential
    integers, so there is nothing to guess.

    Returns True only if the hash is present and matches.
    """
    supplied = (data.get('hash') or data.get('Hash') or '').upper()
    if not supplied:
        return False

    present = [k for k in CALLBACK_FIELD_ORDER if k in data]
    if len(present) == len(CALLBACK_FIELD_ORDER):
        if _hash_fields(data, CALLBACK_FIELD_ORDER) == supplied:
            return True

    # Fall back to the order Paynow actually sent, for forward compatibility
    # with fields we do not yet know about.
    return _hash_fields(data) == supplied
