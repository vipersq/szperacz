import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from telethon.errors import RPCError
from telethon.tl import functions, types

from szperacz import config as cfgmod
from szperacz import service
from szperacz.connectors.telegram import TelegramConnector, parse_public_ref
from szperacz.index import Index
from szperacz.models import ChatInfo, Message
from szperacz.sync import ChatFilter, sync

D = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


def chan(cid, title, username=None, megagroup=False):
    return types.Channel(id=cid, title=title, photo=types.ChatPhotoEmpty(), date=D,
                         username=username, megagroup=megagroup)


def custom_msg(mid, text, chat, chat_id):
    return NS(id=mid, message=text, date=D, action=None, file=None, sender=None, post=True,
              sender_id=None, chat=chat, chat_id=chat_id)


class FakeClient:
    def __init__(self, remains=3, premium=True, posts=None, found=None, mine=None, inside=None):
        self.remains, self.premium = remains, premium
        self.posts = posts or []
        self.found = found or []
        self.mine = mine or []
        self.inside = inside or {}
        self.calls = []

    async def __call__(self, req):
        self.calls.append(type(req).__name__)
        if isinstance(req, functions.channels.CheckSearchPostsFloodRequest):
            if not self.premium:
                raise RPCError(None, "PREMIUM_ACCOUNT_REQUIRED", 403)
            return types.SearchPostsFlood(total_daily=10, remains=self.remains, stars_amount=20,
                                          wait_till=None, query_is_free=False)
        if isinstance(req, functions.channels.SearchPostsRequest):
            self.last_posts_req = req
            ch = chan(500, "BBC News", "bbcnews")
            msgs = [types.Message(id=i, peer_id=types.PeerChannel(500), date=D, message=t)
                    for i, t in self.posts]
            return types.messages.Messages(messages=msgs, chats=[ch], users=[], topics=[])
        if isinstance(req, functions.contacts.SearchRequest):
            return NS(chats=self.found, users=[])
        raise AssertionError(req)

    async def iter_messages(self, entity, search=None, limit=None, **kw):
        if entity is None:
            for m in self.mine:
                yield m
        else:
            for m in self.inside.get(entity.id, []):
                yield m


def conn(client):
    c = TelegramConnector(1, "h", "/tmp/x")
    c.client = client
    return c


def test_parse_public_ref():
    assert parse_public_ref("@g5srs_club") == "g5srs_club"
    assert parse_public_ref("https://t.me/s/rsgb_main/12") == "rsgb_main"
    assert parse_public_ref("https://t.me/+AbCdEf") is None
    assert parse_public_ref("t.me/joinchat/xyz") is None


def test_global_all_sources_and_dedupe():
    grp = chan(700, "Antena Polska", "antenapl", megagroup=True)
    fc = FakeClient(posts=[(1, "nowa antena na 40m"), (2, "antena yagi")], found=[grp],
                    mine=[custom_msg(9, "moja antena", NS(title="SADARS", first_name=None), -1009)],
                    inside={700: [custom_msg(5, "antena w grupie", grp, -100700),
                                  custom_msg(5, "antena w grupie", grp, -100700)]})
    r = asyncio.run(conn(fc).global_search("antena"))
    texts = [m.text for m in r.messages]
    assert "nowa antena na 40m" in texts and "moja antena" in texts
    assert texts.count("antena w grupie") == 1
    assert r.discovered[0].name == "Antena Polska"
    bbc = next(m for m in r.messages if m.chat_name == "BBC News")
    assert bbc.link == "https://t.me/bbcnews/1" and "wszystkie publiczne" in bbc.chat_type
    assert any("Darmowych wyszukiwań na dziś: 3/10" in n for n in r.notes)


def test_quota_exhausted_never_pays_automatically():
    fc = FakeClient(remains=0, posts=[(1, "x antena")])
    r = asyncio.run(conn(fc).global_search("antena", discover=False, mine=False))
    assert "SearchPostsRequest" not in fc.calls
    assert r.payment_needed == 20 and r.messages == []
    # świadoma zgoda -> płatne wyszukiwanie
    r = asyncio.run(conn(fc).global_search("antena", allow_paid_stars=20))
    assert fc.last_posts_req.allow_paid_stars == 20 and len(r.messages) == 1


def test_no_premium_falls_back():
    fc = FakeClient(premium=False, mine=[custom_msg(9, "antena", NS(title="X", first_name=None), -1)])
    r = asyncio.run(conn(fc).global_search("antena", discover=False))
    assert any("Premium" in n for n in r.notes)
    assert [m.text for m in r.messages] == ["antena"]


def test_hashtag_skips_quota():
    fc = FakeClient(remains=0, posts=[(1, "#hamradio contest")])
    r = asyncio.run(conn(fc).global_search("#hamradio"))
    assert "CheckSearchPostsFloodRequest" not in fc.calls
    assert fc.last_posts_req.hashtag == "hamradio" and fc.last_posts_req.query is None
    assert len(r.messages) == 1


def test_service_global_to_hits(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(cfgmod.TEMPLATE, encoding="utf-8")
    cfg = cfgmod.load(str(p))
    cfg.ai.api_key = None
    fc = FakeClient(posts=[(1, "Postawiłem antenę")])
    r = asyncio.run(service.global_search(cfg, conn(fc), "antenę", discover=False, mine=False))
    assert r.hits[0].link == "https://t.me/bbcnews/1" and "«antenę»" in r.hits[0].snippet


class WatchConn:
    name = "telegram"

    def __init__(self):
        self.limits = {}

    async def list_chats(self, watched=None, log=None):
        out = [ChatInfo("telegram", "1", "Moja grupa", "grupa")]
        if watched:
            out.append(ChatInfo("telegram", "-100500", "BBC News", "kanał", watched=True))
        return out

    async def fetch(self, chat, after_id=0, newest_limit=None):
        self.limits[chat.name] = newest_limit
        yield Message("telegram", chat.id, chat.name, 1, D, "wiadomość")


def test_sync_watched_channels(tmp_path):
    ix = Index(":memory:")
    wc = WatchConn()
    flt = ChatFilter([], [], skip_channels=True)   # kanały pomijane — ale obserwowane nie
    s = asyncio.run(sync(wc, ix, flt, watched=["@bbcnews"], watched_limit=300, log=lambda *_: None))
    assert s["chats"] == 2 and wc.limits["BBC News"] == 300 and wc.limits["Moja grupa"] is None


def test_add_watched(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(cfgmod.TEMPLATE, encoding="utf-8")
    assert cfgmod.add_watched(p, "@bbcnews")
    assert not cfgmod.add_watched(p, "https://t.me/bbcnews")
    assert cfgmod.load(str(p)).telegram.watched == ["@bbcnews"]
