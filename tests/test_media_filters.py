import sqlite3
from datetime import datetime, timezone

from telethon.tl import types

from szperacz import service
from szperacz.connectors.telegram import _convert, media_info
from szperacz.index import Index
from szperacz.models import Message, Query


def D(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def test_media_info_webpage_full_description():
    wp = types.WebPage(id=1, url="https://rsgb.org/x", display_url="rsgb.org/x", hash=0,
                       site_name="RSGB", title="Nowe pasmo 40m", description="Pełny opis artykułu " * 5)
    kind, extra = media_info(types.MessageMediaWebPage(webpage=wp))
    assert kind == "webpage" and extra["title"] == "Nowe pasmo 40m" and extra["site"] == "RSGB"
    assert extra["description"].startswith("Pełny opis artykułu")


def test_media_info_document_kinds():
    vid = types.Document(id=1, access_hash=1, file_reference=b"", date=None, mime_type="video/mp4", size=5000,
                         dc_id=1, attributes=[types.DocumentAttributeVideo(duration=12, w=1, h=1),
                                              types.DocumentAttributeFilename("film.mp4")])
    kind, extra = media_info(types.MessageMediaDocument(document=vid))
    assert kind == "video" and extra["file"] == "film.mp4" and extra["duration"] == 12
    voice = types.Document(id=2, access_hash=1, file_reference=b"", date=None, mime_type="audio/ogg", size=10,
                           dc_id=1, attributes=[types.DocumentAttributeAudio(duration=3, voice=True)])
    assert media_info(types.MessageMediaDocument(document=voice))[0] == "voice"


def test_convert_photo_without_caption_and_poll():
    from types import SimpleNamespace as NS
    m = NS(id=5, message="", date=D("2026-11-01T10:00:00"), action=None, sender=None, post=True,
           sender_id=None, out=True, media=types.MessageMediaPhoto())
    msg = _convert(m, "-1001", "Kanał", "kanał", None)
    assert msg.media == "photo" and msg.text == "[zdjęcie]" and msg.is_out
    poll = types.Poll(id=1, hash=0, question=types.TextWithEntities("Który dipol?", []),
                      answers=[types.PollAnswer(types.TextWithEntities("NVIS", []), b"1"),
                               types.PollAnswer(types.TextWithEntities("Inverted V", []), b"2")])
    m2 = NS(id=6, message="Głosujcie", date=D("2026-11-01T10:00:00"), action=None, sender=None, post=True,
            sender_id=None, out=False, media=types.MessageMediaPoll(poll=poll, results=types.PollResults()))
    msg2 = _convert(m2, "-1001", "Kanał", "kanał", None)
    assert "Który dipol?" in msg2.text and "Inverted V" in msg2.text and msg2.media == "poll"


def test_migrates_old_database(tmp_path):
    p = tmp_path / "old.sqlite"
    db = sqlite3.connect(p)   # baza z wersji 0.1 — bez kolumn media/extra/is_out
    db.executescript("""
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, chat_id TEXT NOT NULL,
            chat_name TEXT NOT NULL, chat_type TEXT, msg_id INTEGER NOT NULL, date TEXT NOT NULL, sender_id TEXT,
            sender_name TEXT, text TEXT NOT NULL, link TEXT, UNIQUE (source, chat_id, msg_id));
        INSERT INTO messages (source, chat_id, chat_name, msg_id, date, text) VALUES
            ('telegram','1','Stary czat',1,'2026-10-01T10:00:00+00:00','stara antena');
    """)
    db.close()
    ix = Index(p)
    hits = ix.search(Query(chats=["stary"]))
    assert len(hits) == 1 and hits[0].media is None
    ix.add([Message("telegram", "1", "Stary czat", 2, D("2026-11-01T10:00:00"), "nowa", media="photo")])
    assert ix.search(Query(content=["photo"]))[0].msg_id == 2


def idx():
    ix = Index(":memory:")
    ix.add([
        Message("telegram", "1", "SADARS", 1, D("2026-11-01T08:30:00"), "rano antena", chat_type="grupa",
                sender_name="Marek"),
        Message("telegram", "1", "SADARS", 2, D("2026-11-01T23:30:00"), "noc antena", chat_type="grupa",
                sender_name="Marek", media="photo"),
        Message("telegram", "2", "BBC", 3, D("2026-11-03T12:00:00"), "antena https://bbc.co.uk",
                chat_type="kanał", media="webpage"),
        Message("telegram", "3", "Marek", 4, D("2026-11-04T12:00:00"), "moja antena", chat_type="prywatny",
                is_out=True, media="video"),
    ])
    return ix


def ids(hits):
    return sorted(h.msg_id for h in hits)


def test_filters_hours_types_content_direction_order():
    ix = idx()
    g = [["anten*"]]
    assert ids(ix.search(Query(keyword_groups=g, hour_from=8, hour_to=9))) == [1]
    assert ids(ix.search(Query(keyword_groups=g, hour_from=22, hour_to=6))) == [2]      # przez północ
    assert ids(ix.search(Query(keyword_groups=g, chat_types=["kanał", "prywatny"]))) == [3, 4]
    assert ids(ix.search(Query(keyword_groups=g, content=["photo"]))) == [2]
    assert ids(ix.search(Query(keyword_groups=g, content=["link"]))) == [3]
    assert ids(ix.search(Query(keyword_groups=g, content=["video"]))) == [4]
    assert ids(ix.search(Query(keyword_groups=g, content=["text"]))) == [1, 3]
    assert ids(ix.search(Query(keyword_groups=g, direction="out"))) == [4]
    assert ids(ix.search(Query(keyword_groups=g, direction="in"))) == [1, 2, 3]
    assert [h.msg_id for h in ix.search(Query(keyword_groups=g, order="oldest"))] == [1, 2, 3, 4]
    assert [h.msg_id for h in ix.search(Query(keyword_groups=g, order="date"))] == [4, 3, 2, 1]
    assert ix.search(Query(keyword_groups=g, content=["photo"]))[0].chat_id == "1"


def test_message_matches_for_live_results():
    m = Message("telegram", "9", "UK Rail · wszystkie publiczne", 1, D("2026-11-05T09:00:00"), "strike",
                chat_type="kanał · wszystkie publiczne", media="photo")
    assert service.message_matches(m, Query(chat_types=["kanał"], content=["photo"]))
    assert not service.message_matches(m, Query(date_from="2026-11-06"))
    assert not service.message_matches(m, Query(direction="out"))
    assert service.message_matches(m, Query(hour_from=8, hour_to=10))
