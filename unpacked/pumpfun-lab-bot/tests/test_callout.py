from types import SimpleNamespace

from src.callout import PumpFunCalloutPublisher


class DummyDB:
    def record_callout(self, **kwargs):
        return 1

    def record_callout_result(self, *args, **kwargs):
        pass

    def journal(self, *args, **kwargs):
        pass


def settings(**overrides):
    base = dict(
        call_outs_enabled=True,
        pumpfun_callout_mode="PLAYWRIGHT",
        pumpfun_callout_accept_terms=True,
        pumpfun_callout_profile_dir="data/pumpfun-profile",
        pumpfun_callout_headless=True,
        pumpfun_callout_create_url="https://pump.fun/create",
        pumpfun_callout_timeout_ms=30000,
        pumpfun_callout_template="{tagline} BOUGHT ${symbol} {amount_sol} {mint} {pump_url}",
        pumpfun_tagline="BUILDING IN PUBLIC // SYNTHETICMIND",
        telegram_trade_name="Zepto",
        telegram_profile_url="https://pump.fun/profile/TEST",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_callout_message_contains_position_disclosure():
    publisher = PumpFunCalloutPublisher(settings(), DummyDB())
    msg = publisher._message(
        name="Example",
        symbol="EX",
        mint="Mint111",
        amount=0.005,
        signature="Sig111",
    )
    assert "BOUGHT $EX" in msg
    assert "0.005000" in msg
    assert "Mint111" in msg
    assert "pump.fun/coin/Mint111" in msg
