import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

from szperacz import ai
from szperacz.cli import main, search_with_relax
from szperacz.connectors.base import Connector
from szperacz.index import Index, build_fts, fold, make_snippet
from szperacz.models import ChatInfo, Message, Query
from szperacz.sync import ChatFilter, sync


def M(i, text, chat="SADARS", sender="Marek", d="2026-10-12", chat_id="1"):
    return Message(source="telegram", chat_id=chat_id, chat_name=chat, chat_type="grupa",
                   msg_id=i, date=datetime.fromisoformat(d + "T10:00:00").replace(tzinfo=timezone.utc),
                   text=text, sender_name=sender, sender_id="7")


def idx():
    ix = Index(":memory:")
    ix.add([
        M(1, "Postawiłem nową antenę na 40m, dipol NVIS"),
        M(2, "Spotkanie klubowe w czwartek o 19", sender="Łukasz Żółć"),
        M(3, "Antenna tuner arrived", chat="Ham Shack", chat_id="2", d="2026-09-01"),
        M(4, "Przekaźnik GB3SN znowu nie działa", sender="Ania", d="2026-11-01"),
        M(5, "zółw na torach w Salisbury", chat="SWR Guards", chat_id="3"),
    ])
    return ix


def test_fold():
    assert fold("Łukasz Żółć") == "lukasz zolc"
    assert len(fold("Łukasz Żółć")) == len("Łukasz Żółć")


def test_polish_inflection_and_diacritics():
    ix = idx()
    assert [h.row_id for h in ix.search(Query(keyword_groups=[["anten*"]]))] != []
    got = {h.text for h in ix.search(Query(keyword_groups=[["anten*"]]))}
    assert "Antenna tuner arrived" in got and any("antenę" in t for t in got)
    # bez polskich znaków znajduje z polskimi
    assert ix.search(Query(keyword_groups=[["przekaznik"]]))
    assert ix.search(Query(keyword_groups=[["postawilem"]]))
    # ł -> l
    assert ix.search(Query(keyword_groups=[["zolw"]]))
    assert ix.search(Query(keyword_groups=[["lukasz"]]))  # w nadawcy


def test_filters():
    ix = idx()
    assert len(ix.search(Query(senders=["lukasz"]))) == 1
    assert len(ix.search(Query(chats=["sadars"]))) == 3
    r = ix.search(Query(date_from="2026-10-01", date_to="2026-10-31"))
    assert {h.row_id for h in r} == {1, 2, 5}
    assert len(ix.search(Query(keyword_groups=[["anten*"]], chats=["ham"]))) == 1


def test_and_or_relax():
    ix = idx()
    q = Query(keyword_groups=[["anten*"], ["gb3sn"]])
    hits, relaxed = search_with_relax(ix, q)
    assert relaxed and len(hits) == 3


def test_fts_injection_safe():
    ix = idx()
    # cudzysłowy i operatory w słowach nie psują zapytania
    ix.search(Query(keyword_groups=[['anten" OR *'], ["NEAR("]]))
    assert build_fts([['a"b']]) == '"a b"'


def test_snippet_keeps_original_text():
    s = make_snippet("Postawiłem nową antenę na 40m", [["anten*"], ["postawilem"]])
    assert "«antenę»" in s and "«Postawiłem»" in s


def test_upsert_edit():
    ix = idx()
    ix.add([M(1, "Edytowane: antena padła")])
    assert ix.search(Query(keyword_groups=[["padla"]]))
    assert ix.stats()["total"] == 5


class FakeConn(Connector):
    name = "telegram"

    def __init__(self, data):
        self.data = data  # chat_id -> list[Message]

    async def list_chats(self):
        return [ChatInfo("telegram", "1", "SADARS", "grupa"),
                ChatInfo("telegram", "9", "Wiadomości BBC", "kanał")]

    async def fetch(self, chat, after_id=0, newest_limit=None):
        msgs = sorted(self.data.get(chat.id, []), key=lambda m: m.msg_id)
        if after_id == 0 and newest_limit:
            msgs = msgs[-newest_limit:][::-1]
        else:
            msgs = [m for m in msgs if m.msg_id > after_id]
        for m in msgs:
            yield m


def test_sync_incremental_and_filters():
    ix = Index(":memory:")
    data = {"1": [M(i, f"wiadomość {i}") for i in range(1, 11)],
            "9": [M(1, "news", chat="Wiadomości BBC", chat_id="9")]}
    conn = FakeConn(data)
    flt = ChatFilter(include=[], exclude=[], skip_channels=True)
    s = asyncio.run(sync(conn, ix, flt, first_sync_limit=3, log=lambda *_: None))
    assert s["new"] == 3 and ix.get_last_id("telegram", "1") == 10
    data["1"].append(M(11, "nowa"))
    s = asyncio.run(sync(conn, ix, flt, log=lambda *_: None))
    assert s["new"] == 1 and ix.stats()["total"] == 4


def test_chat_filter():
    c = ChatInfo("telegram", "5", "Rodzina Łódź", "grupa")
    assert ChatFilter(["lodz"], []).allows(c)
    assert not ChatFilter([], ["rodzina"]).allows(c)


class FakeClaude:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if kw.get("tools"):
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=self.payload)])
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="Marek postawił antenę [1].")])


def test_ai_parse_and_answer():
    fake = FakeClaude({"keyword_groups": [["anten*", "antenna*"]], "chats": [], "senders": ["Marek"],
                       "date_from": "2026-10-01", "date_to": "2026-10-31", "order": "rank"})
    q = ai.parse_prompt("co Marek pisał o antenie w październiku", api_key=None, model="m",
                        known_chats=["SADARS"], today=date(2026, 11, 4), client=fake)
    assert q.senders == ["Marek"] and q.date_from == "2026-10-01"
    assert "2026-11-04" in fake.calls[0]["system"]
    hits = idx().search(q)
    assert len(hits) == 1
    assert "[1]" in ai.answer("x", hits, api_key=None, model="m", client=fake)


def test_cli_szukaj(tmp_path, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[ogolne]\nkatalog_danych = "dane"\n', encoding="utf-8")
    ix = Index(tmp_path / "dane" / "indeks.sqlite")
    ix.add([M(1, "Postawiłem nową antenę")])
    ix.close()
    assert main(["--config", str(cfg), "szukaj", "anten*"]) == 0
    assert "«antenę»" in capsys.readouterr().out
    assert main(["--config", str(cfg), "pytaj", "cos"]) == 2  # brak klucza API
    assert main(["--config", str(cfg), "login"]) == 2          # brak api_id
