from szperacz.shield import classify, hashtag_wall, is_public_origin

SPAM = [  # prawdziwe przykłady z wyszukiwania #kraków
    ("кая #tieling #kraków", "🌀 FlirtRoom", "photo"),
    ("#kraków #мукачево #strassgang", "Leadership 101 for Hum", "photo"),
    ("#druckerei #kraków #haddiyan", "꒰ ✿ ✿ Eva ꒱", "photo"),
    ("#kraków #sıkılır #naporem", "⸜🪶⸝ Jennifer Parker ⸜🪶⸝", "photo"),
    ("енгой #rabat #kraków", "Paulina Online ✿", "photo"),
    ("#lang #kraków #zākhū", "😈 love heat", "photo"),
]
OK = [
    ("Shooting reported near the Main Market Square in Kraków, Poland. At least four people were hit", "Faytuks Network", ""),
    ("Utrudnienia w ruchu na Alejach — remont torowiska potrwa do piątku. #kraków #mpk", "Kraków Info", ""),
    ("Spotkanie klubowe w czwartek o 19 #sadars #krótkofalowcy #kraków", "SADARS", "photo"),
]


def test_spam_examples_hidden():
    for text, chat, media in SPAM:
        assert classify(text, chat, chat, media, public_origin=True)[0] in ("spam", "dorosli"), chat


def test_legit_examples_pass():
    for text, chat, media in OK:
        assert classify(text, chat, chat, media, public_origin=True)[0] is None, chat


def test_adult_and_scam_words_everywhere():
    assert classify("Nowe dziewczyny 18+ zapraszamy", "x", "", None, False)[0] == "dorosli"
    assert classify("Kasyno online — darmowe spiny!", "x", "", None, False)[0] == "spam"


def test_own_chats_not_flagged_by_hashtags():
    assert classify("#kraków #fun #love", "Moja grupa", "Ania", "photo", public_origin=False)[0] is None


def test_helpers():
    assert hashtag_wall("#a1 #b2 #c3") and not hashtag_wall("Długi tekst o czymś ważnym w mieście #a #b #c")
    assert is_public_origin("kanał · wszystkie publiczne") and not is_public_origin("grupa")
