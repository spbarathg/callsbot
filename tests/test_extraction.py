import re
from types import SimpleNamespace
from bot.telegram import extract_contract_addresses_from_message, normalize_text_for_extraction


class _Msg:
    def __init__(self, text: str):
        self.text = text
        self.id = 1
        self.entities = []


class _Event:
    def __init__(self, raw_text: str):
        self.raw_text = raw_text
        self.message = _Msg(raw_text)


def _ev(text: str):
    return _Event(text)


def test_normalize_quotes():
    s = 'CA “AbCd” and ‘efGh’'
    out = normalize_text_for_extraction(s)
    assert '"' in out and "'" in out


def test_extract_basic_base58():
    ca = '9wYucdoBb1CV7DcxG1cdKGn6XPHi3QBjyvhb1WejG7Hw'
    ev = _ev(f"Token: {ca}")
    addrs = extract_contract_addresses_from_message(ev)
    assert ca in addrs


