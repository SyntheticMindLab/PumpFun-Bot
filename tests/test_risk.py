from src.risk import evaluate_gates


def test_gate_blocks_high_market_cap():
    token = {
        "market_cap_sol": 300,
        "initial_buy": 30,
        "v_tokens": 1000,
        "risk_score": 10,
        "liquidity_usd": 50000,
        "mint_authority": None,
        "freeze_authority": None,
    }
    rules = {
        "gates": {
            "max_market_cap_sol": 250,
            "min_market_cap_sol": 20,
            "min_initial_buy_ratio": 0.005,
            "require_rugcheck": True,
            "max_rugcheck_score": 35,
            "min_liquidity_usd": 0,
            "require_mint_authority_disabled": True,
            "require_freeze_authority_disabled": True,
        },
        "scoring": {"weights": {"market_cap": 20, "initial_buy": 20, "rugcheck": 40, "liquidity": 20},
                    "thresholds": {"market_cap_ideal_low": 30, "market_cap_ideal_high": 150,
                                   "initial_buy_ratio_ideal": 0.02, "liquidity_ideal_usd": 25000}},
    }
    passed, score, reasons = evaluate_gates(token, rules)
    assert not passed
    assert "market cap above hard ceiling" in reasons
    assert 0 <= score <= 100


def test_gate_passes_clean_token():
    token = {
        "market_cap_sol": 80,
        "initial_buy": 30,
        "v_tokens": 1000,
        "risk_score": 10,
        "liquidity_usd": 50000,
        "mint_authority": None,
        "freeze_authority": None,
    }
    rules = {
        "gates": {
            "max_market_cap_sol": 250,
            "min_market_cap_sol": 20,
            "min_initial_buy_ratio": 0.005,
            "require_rugcheck": True,
            "max_rugcheck_score": 35,
            "min_liquidity_usd": 0,
            "require_mint_authority_disabled": True,
            "require_freeze_authority_disabled": True,
        },
        "scoring": {"weights": {"market_cap": 20, "initial_buy": 20, "rugcheck": 40, "liquidity": 20},
                    "thresholds": {"market_cap_ideal_low": 30, "market_cap_ideal_high": 150,
                                   "initial_buy_ratio_ideal": 0.02, "liquidity_ideal_usd": 25000}},
    }
    passed, _, _ = evaluate_gates(token, rules)
    assert passed
