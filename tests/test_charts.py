from ui.charts import tradingview_symbol_for_mt5


def test_tradingview_symbol_maps_forex_pairs_and_broker_suffixes():
    assert tradingview_symbol_for_mt5("EURUSD") == "FX:EURUSD"
    assert tradingview_symbol_for_mt5("EURUSDm") == "FX:EURUSD"


def test_tradingview_symbol_maps_metals_and_preserves_other_tickers():
    assert tradingview_symbol_for_mt5("XAUUSDm") == "OANDA:XAUUSD"
    assert tradingview_symbol_for_mt5("BTCUSD") == "BTCUSD"
