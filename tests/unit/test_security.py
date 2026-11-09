"""安全层测试：输入校验、路径越界、CSV 注入、脱敏与本地保险箱。"""

from __future__ import annotations

import json
import unittest

from tradesome.core.errors import SecurityError, ValidationError
from tradesome.security.redaction import looks_secret, mask_secret, redact_text
from tradesome.security.validation import (clamp_text, is_safe_text,
                                           safe_join, sanitize_csv_cell,
                                           sanitize_csv_row, validate_filename,
                                           validate_symbol)
from tradesome.security.vault import SecretVault
from tests.support import TempWorkspace


class SymbolValidationTest(unittest.TestCase):
    def test_accepts_standard_codes(self) -> None:
        self.assertEqual(validate_symbol("600519.SH"), "600519.SH")
        self.assertEqual(validate_symbol(" 000001.sz "), "000001.SZ")
        self.assertEqual(validate_symbol("430047.BJ"), "430047.BJ")

    def test_rejects_bad_codes(self) -> None:
        for bad in ("", "60051", "600519.XX", "600519", "600519.SH;", "../600519.SH"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    validate_symbol(bad)


class FilenameValidationTest(unittest.TestCase):
    def test_accepts_plain_names(self) -> None:
        self.assertEqual(validate_filename("我的策略.py", ".py"), "我的策略.py")

    def test_rejects_path_characters(self) -> None:
        for bad in ("a/b.py", "a\\b.py", "a:b.py", "a*b.py", "..", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    validate_filename(bad)

    def test_rejects_wrong_suffix(self) -> None:
        with self.assertRaises(ValidationError):
            validate_filename("strategy.txt", ".py")


class SafeJoinTest(unittest.TestCase):
    def test_join_inside_base(self) -> None:
        with TempWorkspace() as tmp:
            target = safe_join(tmp.root, "sub", "file.csv")
            self.assertTrue(str(target).startswith(str(tmp.root.resolve())))

    def test_rejects_absolute_and_drive_paths(self) -> None:
        with TempWorkspace() as tmp:
            for bad in ("C:\\Windows\\system.ini", "/etc/passwd"):
                with self.subTest(bad=bad):
                    with self.assertRaises(SecurityError):
                        safe_join(tmp.root, bad)

    def test_rejects_parent_traversal(self) -> None:
        with TempWorkspace() as tmp:
            for bad in ("..", "../outside.txt", "a/../../b.txt"):
                with self.subTest(bad=bad):
                    with self.assertRaises(SecurityError):
                        safe_join(tmp.root, bad)

    def test_empty_parts_are_skipped(self) -> None:
        with TempWorkspace() as tmp:
            self.assertEqual(safe_join(tmp.root, "", "a.txt"),
                             tmp.root.resolve() / "a.txt")


class CsvInjectionTest(unittest.TestCase):
    def test_formula_prefixes_are_quoted(self) -> None:
        for value in ("=1+1", "+1", "-1", "@SUM(A1)", "\tcmd"):
            with self.subTest(value=value):
                self.assertTrue(sanitize_csv_cell(value).startswith("'"))

    def test_plain_values_untouched(self) -> None:
        self.assertEqual(sanitize_csv_cell("茅台"), "茅台")
        self.assertEqual(sanitize_csv_cell(None), "")
        self.assertEqual(sanitize_csv_cell(12.5), "12.5")

    def test_row(self) -> None:
        self.assertEqual(sanitize_csv_row(["=cmd", 1]),
                         ["'=cmd", "1"])

    def test_text_helpers(self) -> None:
        self.assertTrue(is_safe_text("正常文本"))
        self.assertFalse(is_safe_text("a\x00b"))
        self.assertTrue(clamp_text("x" * 50, 10).endswith("…"))


class RedactionTest(unittest.TestCase):
    def test_redacts_assignments(self) -> None:
        text = redact_text("api_key=abc123 token: xyz password = p@ss")
        self.assertNotIn("abc123", text)
        self.assertNotIn("xyz", text)
        self.assertNotIn("p@ss", text)
        self.assertIn("***", text)

    def test_redacts_bearer(self) -> None:
        text = redact_text("Authorization: Bearer abcdefghijklmnop")
        self.assertNotIn("abcdefghijklmnop", text)

    def test_mask_secret(self) -> None:
        self.assertEqual(mask_secret("abcdefghijkl"), "abc****ijkl")
        self.assertEqual(mask_secret("abc"), "***")
        self.assertEqual(mask_secret(""), "")

    def test_looks_secret(self) -> None:
        self.assertTrue(looks_secret("api_key"))
        self.assertTrue(looks_secret("Access-Token"))
        self.assertFalse(looks_secret("symbol"))


class VaultTest(unittest.TestCase):
    def test_roundtrip_and_delete(self) -> None:
        with TempWorkspace() as tmp:
            path = tmp.root / "secrets.dat"
            vault = SecretVault(path)
            vault.set("api_key", "sk-test-123456")
            self.assertEqual(vault.get("api_key"), "sk-test-123456")
            self.assertIn("api_key", vault.keys())
            self.assertTrue(vault.delete("api_key"))
            self.assertFalse(vault.delete("api_key"))
            self.assertIsNone(vault.get("api_key"))
            vault.set("a", "1")
            vault.clear()
            self.assertEqual(vault.keys(), [])

    def test_file_does_not_contain_plaintext(self) -> None:
        with TempWorkspace() as tmp:
            path = tmp.root / "secrets.dat"
            vault = SecretVault(path)
            vault.set("token", "super-secret-value")
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("super-secret-value", raw)
            payload = json.loads(raw)
            self.assertEqual(payload["version"], 1)
            self.assertIn(payload["mode"], ("dpapi", "fallback"))

    def test_disabled_vault_refuses_writes(self) -> None:
        with TempWorkspace() as tmp:
            vault = SecretVault(tmp.root / "secrets.dat", enabled=False)
            self.assertFalse(vault.available())
            self.assertIsNone(vault.get("any"))
            with self.assertRaises(SecurityError):
                vault.set("any", "value")

    def test_empty_key_rejected(self) -> None:
        with TempWorkspace() as tmp:
            vault = SecretVault(tmp.root / "secrets.dat")
            with self.assertRaises(SecurityError):
                vault.set("  ", "value")

    def test_corrupted_file_raises_clear_error(self) -> None:
        with TempWorkspace() as tmp:
            path = tmp.root / "secrets.dat"
            path.write_text("not-json", encoding="utf-8")
            vault = SecretVault(path)
            with self.assertRaises(SecurityError):
                vault.keys()

    def test_description_mentions_protection_mode(self) -> None:
        with TempWorkspace() as tmp:
            vault = SecretVault(tmp.root / "secrets.dat")
            description = vault.description()
            self.assertTrue(description)
            self.assertEqual(vault.weak_protection, vault.mode != "dpapi")


if __name__ == "__main__":
    unittest.main()
