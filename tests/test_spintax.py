import re

from conftest import load_meta

spintax = load_meta("spintax").SpintaxEngine
email = load_meta("email_campaign")


def test_parse_expands_nested_braces():
    for _ in range(20):
        assert spintax.parse("{a|{b|c}}") in {"a", "b", "c"}


def test_generate_hook_has_no_placeholders():
    for niche in (*spintax.NICHES, "unknown_niche", None):
        for platform in ("IG", "FB"):
            text = spintax.generate_hook(niche, platform)
            assert not re.search(r"WHAT|ACTION|SOURCE|HERE|[{}|]", text), text


def test_generate_hook_matches_platform():
    assert "Instagram" in spintax.generate_hook("rental", "IG")
    assert "Messenger" in spintax.generate_hook("rental", "FB")


def test_email_spin_keeps_named_placeholders():
    assert email.spin("{Hi|Hi} {company}") == "Hi {company}"


def test_email_render_fills_values():
    template = {"subjects": ["Hi [BUSINESS]"], "body": "Hello\n{sender}\n{demo_link}"}
    settings = {"sender_name": "Sam"}
    subject, body = email.render_message({"name": "Acme | Best in town", "demo_link": "http://x"}, template, settings)
    assert subject == "Hi Acme"
    assert body == "Hello\nSam\nhttp://x"


def test_email_niche_override_wins():
    template = {"subjects": ["base"], "body": "base", "niches": {"renovation": {"subjects": ["reno"], "body": "reno body"}}}
    subject, body = email.render_message({"name": "A", "hook_niche": "renovation"}, template, {"sender_name": ""})
    assert (subject, body) == ("reno", "reno body")


def test_shipped_email_template_is_neutral_and_renders():
    template = email.load_template()
    assert "Ivan" not in str(template)
    subject, body = email.render_message({"name": "Acme"}, template, {"sender_name": "Sam"})
    assert subject and "Sam" in body and "[" not in body
