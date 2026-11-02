from conftest import load_meta

utils = load_meta("utils")


def test_messenger_links_are_not_websites():
    for url in ("https://fb.me/abc", "https://m.me/page", "https://api.whatsapp.com/send?phone=1", "https://www.messenger.com/t/x"):
        assert utils.is_custom_website(url, [])[0] is False, url


def test_real_domain_is_still_a_website():
    assert utils.is_custom_website("https://dreamdoorskitchens.com.au", ["facebook.com"])[0] is True
