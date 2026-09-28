import unittest

from trading_platform.symbols import AssetClass, SymbolRegistry


class SymbolRegistryTests(unittest.TestCase):
    def test_symbol_registry_requires_explicit_broker_mapping(self) -> None:
        registry = SymbolRegistry()
        self.assertEqual(registry.get("xauusd").asset_class, AssetClass.GOLD)
        with self.assertRaisesRegex(ValueError, "no broker mapping"):
            registry.broker_symbol("XAUUSD", {})
        self.assertEqual(
            registry.broker_symbol("XAUUSD", {"XAUUSD": "GOLD.pro"}), "GOLD.pro"
        )

    def test_unknown_canonical_symbol_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown canonical symbol"):
            SymbolRegistry().get("NOTREAL")

    def test_empty_broker_mapping_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "empty broker mapping"):
            SymbolRegistry().broker_symbol("EURUSD", {"EURUSD": "  "})


if __name__ == "__main__":
    unittest.main()
