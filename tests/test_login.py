import asyncio

import pytest
from telethon import errors
from telethon.tl import functions, types
from telethon.tl.types import auth as A

from szperacz.connectors.telegram import RESEND, LoginNeedsEmail, TelegramConnector, describe_sent_code


def sent(t, next_type=None, timeout=None):
    return A.SentCode(type=t, phone_code_hash="h", next_type=next_type, timeout=timeout)


class FakeAuth:
    def __init__(self, first, resend=None, good="12345", need_2fa=False, pw="tajne"):
        self.first, self.resend_result = first, resend
        self.good, self.need_2fa, self.pw = good, need_2fa, pw
        self.authorized = False
        self.calls = []

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return types.User(id=1, first_name="Kriss")

    async def __call__(self, req):
        self.calls.append(type(req).__name__)
        if isinstance(req, functions.auth.SendCodeRequest):
            return self.first
        if isinstance(req, functions.auth.ResendCodeRequest):
            return self.resend_result
        if isinstance(req, functions.auth.SignInRequest):   # e-mail
            if req.email_verification.code != self.good:
                raise errors.PhoneCodeInvalidError(None)
            self.authorized = True
            return None
        raise AssertionError(req)

    async def sign_in(self, phone=None, code=None, *, password=None, phone_code_hash=None):
        if password is not None:
            if password != self.pw:
                raise errors.PasswordHashInvalidError(None)
            self.authorized = True
            return
        if code != self.good:
            raise errors.PhoneCodeInvalidError(None)
        if self.need_2fa:
            raise errors.SessionPasswordNeededError(None)
        self.authorized = True


def run_login(fake, codes, passwords=()):
    c = TelegramConnector(1, "h", "/tmp/x", phone="+447700900123")
    c._make_client = lambda: fake
    infos, codes, passwords = [], list(codes), list(passwords)

    def code_cb(info):
        infos.append(info)
        return codes.pop(0)

    name = asyncio.run(c.login(code_cb=code_cb, password_cb=lambda: passwords.pop(0)))
    return name, infos


def test_describe_app_code_mentions_app_not_sms():
    txt = describe_sent_code(sent(A.SentCodeTypeApp(length=5), types.auth.CodeTypeSms(), 60))
    assert "W APLIKACJI TELEGRAM" in txt and "SMS-em" in txt and "60 s" in txt


def test_resend_as_sms_then_login():
    fake = FakeAuth(sent(A.SentCodeTypeApp(length=5), types.auth.CodeTypeSms()),
                    resend=sent(A.SentCodeTypeSms(length=5)))
    name, infos = run_login(fake, [RESEND, "12 345"])
    assert name == "Kriss" and "ResendCodeRequest" in fake.calls
    assert "Wysłano ponownie" in infos[1] and "SMS-em" in infos[1]


def test_wrong_code_retry():
    fake = FakeAuth(sent(A.SentCodeTypeApp(length=5)))
    name, infos = run_login(fake, ["11111", "12345"])
    assert name == "Kriss" and "Nieprawidłowy kod" in infos[1]


def test_email_code():
    fake = FakeAuth(sent(A.SentCodeTypeEmailCode(email_pattern="k***@windowslive.com", length=6)), good="654321")
    name, infos = run_login(fake, ["654321"])
    assert "k***@windowslive.com" in infos[0] and "SignInRequest" in fake.calls


def test_setup_email_required():
    fake = FakeAuth(sent(A.SentCodeTypeSetUpEmailRequired()))
    with pytest.raises(LoginNeedsEmail):
        run_login(fake, [])


def test_2fa():
    fake = FakeAuth(sent(A.SentCodeTypeApp(length=5)), need_2fa=True)
    name, _ = run_login(fake, ["12345"], ["zle", "tajne"])
    assert name == "Kriss"


# ------------------------------------------------------------------ QR
from datetime import datetime, timedelta, timezone

from szperacz.connectors.telegram import QrCancelled


class FakeQrClient:
    """Udaje Telethon: qr_login + zdarzenie UpdateLoginToken po 'zeskanowaniu'."""

    def __init__(self, need_2fa=False, expire_first=False, scan_after=1.2):
        self.need_2fa, self.expire_first, self.scan_after = need_2fa, expire_first, scan_after
        self.handlers = []
        self.recreated = 0
        self.authorized = False
        self.api_id, self.api_hash = 1, "h"

    async def connect(self): pass
    async def disconnect(self): pass
    async def is_user_authorized(self): return self.authorized
    async def get_me(self): return types.User(id=1, first_name="Kriss")

    async def qr_login(self):
        from telethon.tl.custom.qrlogin import QRLogin
        qr = QRLogin(self, [])
        exp = datetime.now(timezone.utc) + (timedelta(seconds=-1) if self.expire_first else timedelta(seconds=30))
        qr._resp = types.auth.LoginToken(expires=exp, token=b"abc")
        asyncio.get_running_loop().call_later(self.scan_after, self._scan)
        return qr

    def _scan(self):
        for h in self.handlers:
            asyncio.ensure_future(h(None))

    def add_event_handler(self, h, ev): self.handlers.append(h)
    def remove_event_handler(self, h): self.handlers.remove(h)

    async def __call__(self, req):
        if isinstance(req, functions.auth.ExportLoginTokenRequest):
            if self.need_2fa:
                raise errors.SessionPasswordNeededError(None)
            return types.auth.LoginTokenSuccess(authorization=types.auth.Authorization(user=types.User(id=1, first_name="Kriss")))
        raise AssertionError(req)

    async def _on_login(self, user):
        self.authorized = True

    async def sign_in(self, password=None, **kw):
        self.authorized = True

    async def _recreate(self):
        self.recreated += 1


def qr_run(fake, cancel_after=None, passwords=()):
    from telethon.tl.custom.qrlogin import QRLogin
    orig = QRLogin.recreate

    async def rec(self):
        fake.recreated += 1
        self._resp = types.auth.LoginToken(expires=datetime.now(timezone.utc) + timedelta(seconds=30), token=b"new")
    QRLogin.recreate = rec
    c = TelegramConnector(1, "h", "/tmp/x")
    c._make_client = lambda: fake
    shown, pw = [], list(passwords)
    t0 = datetime.now()
    try:
        name = asyncio.run(c.qr_login(lambda url, exp: shown.append(url),
                                      lambda: cancel_after is not None and (datetime.now() - t0).total_seconds() > cancel_after,
                                      password_cb=lambda: pw.pop(0)))
    finally:
        QRLogin.recreate = orig
    return name, shown


def test_qr_login_success():
    name, shown = qr_run(FakeQrClient())
    assert name == "Kriss" and shown[0].startswith("tg://login?token=")


def test_qr_refreshes_expired_token():
    fake = FakeQrClient(expire_first=True)
    name, shown = qr_run(fake)
    assert fake.recreated >= 1 and len(shown) >= 2 and name == "Kriss"


def test_qr_2fa():
    name, _ = qr_run(FakeQrClient(need_2fa=True), passwords=["tajne"])
    assert name == "Kriss"


def test_qr_cancel():
    with pytest.raises(QrCancelled):
        qr_run(FakeQrClient(scan_after=60), cancel_after=0.5)
