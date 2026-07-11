from robinhood_pump_bot.storage import Storage


def test_token_is_only_claimed_once(tmp_path):
    db = Storage(tmp_path / "bot.db")
    token = "0xabc0000000000000000000000000000000000000"

    assert db.claim_token_for_scan(token) is True
    assert db.claim_token_for_scan(token.upper()) is False


def test_api_keys_can_be_added_without_limit(tmp_path):
    db = Storage(tmp_path / "bot.db")
    for i in range(25):
        db.add_api_key("blockscout", f"key-{i:02d}-secret")

    keys = db.list_api_keys("blockscout")
    assert len(keys) == 25
    assert keys[0]["masked"].startswith("key-")
    assert "secret" not in keys[0]["masked"]
