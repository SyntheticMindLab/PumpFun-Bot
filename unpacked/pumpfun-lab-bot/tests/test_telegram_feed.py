from src.telegram_feed import buy_message, sell_message, trade_status


def test_buy_message_matches_trade_feed_shape():
    msg = buy_message(
        name="Bantu",
        symbol="BANTU",
        mint="A8ovRFh7tUAR4ST2rkBbashtEGLHLz9LmYxQfF5cpump",
        amount_sol=0.202,
        bank_sol=47.07,
        trade_name="Zepto",
        profile_url="https://pump.fun/profile/TEST",
        footer="every fill called live — nothing cherry-picked",
    )
    assert "Zepto:" in msg
    assert "🟣 APED · BANTU" in msg
    assert "🎯 in for 0.202 SOL" in msg
    assert "`A8ovRFh7tUAR4ST2rkBbashtEGLHLz9LmYxQfF5cpump`" in msg
    assert "bank 47.07 SOL" in msg
    assert "chart" in msg and "gmgn" in msg


def test_sell_status_thresholds():
    assert trade_status(-65.2).endswith("REKT")
    assert trade_status(-31.3).endswith("REKT")
    assert trade_status(-19.0).endswith("CUT IT")
    assert trade_status(12.0).endswith("COOKING")


def test_sell_message_includes_pnl():
    msg = sell_message(
        name="OrangeShark",
        symbol="ORANGE",
        mint="DpWKcbRUxgZnbmvxvs5ZG8CPZfETLTXZFRxzvVCJpump",
        pnl_sol=-0.0022,
        pnl_pct=-19.0,
        bank_sol=47.06,
        trade_name="Zepto",
        profile_url="https://pump.fun/profile/TEST",
        footer="same wallet, same second, every trade",
    )
    assert "CUT IT" in msg
    assert "-0.0022 SOL" in msg
    assert "-19.0%" in msg
    assert "bank 47.06 SOL" in msg
