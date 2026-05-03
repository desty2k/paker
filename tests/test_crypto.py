"""Tests for the encryption layer — _paker_crypto C extension and paker._crypto."""
import os
import sys
import json
import base64
import marshal
import pytest

import paker
from paker._crypto import (
    encrypt,
    decrypt_to_code,
    decrypt_to_unmarshalled,
    decrypt_to_fd,
    scrub,
)

KEY = os.urandom(32)
WRONG_KEY = os.urandom(32)

TESTS_DATA = os.path.join(os.path.dirname(__file__), "tests_data")


class TestEncryptDecryptRoundtrip:
    def test_py_source_roundtrip(self):
        source = "x = 42\ny = x + 1\n"
        blob = encrypt(source, KEY)
        code_obj = decrypt_to_code(blob, KEY, "<test>")
        ns = {}
        exec(code_obj, ns)
        assert ns["x"] == 42
        assert ns["y"] == 43

    def test_pyc_roundtrip_via_unmarshal(self):
        source = "z = 77\n"
        code_obj = compile(source, "<pyc>", "exec", optimize=2)
        blob = encrypt(marshal.dumps(code_obj), KEY)
        out = decrypt_to_unmarshalled(blob, KEY)
        ns = {}
        exec(out, ns)
        assert ns["z"] == 77

    def test_binary_via_fd(self, tmp_path):
        data = os.urandom(1024)
        blob = encrypt(data, KEY)
        path = tmp_path / "out.bin"
        flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        fd = os.open(str(path), flags, 0o600)
        try:
            decrypt_to_fd(blob, KEY, fd)
        finally:
            os.close(fd)
        assert path.read_bytes() == data

    def test_empty_source(self):
        blob = encrypt("", KEY)
        code_obj = decrypt_to_code(blob, KEY, "<empty>")
        ns = {}
        exec(code_obj, ns)

    def test_large_source(self):
        source = "x = 0\n" + "x += 1\n" * 10000
        blob = encrypt(source, KEY)
        code_obj = decrypt_to_code(blob, KEY, "<large>")
        ns = {}
        exec(code_obj, ns)
        assert ns["x"] == 10000

    def test_unicode_source(self):
        source = "msg = 'こんにちは世界'\n"
        blob = encrypt(source, KEY)
        code_obj = decrypt_to_code(blob, KEY, "<unicode>")
        ns = {}
        exec(code_obj, ns)
        assert ns["msg"] == "こんにちは世界"

    def test_bytes_input_via_unmarshal_raw(self, tmp_path):
        """decrypt_to_fd is the way to get plaintext bytes off a blob —
        test that binary data round-trips through fd."""
        data = b"\x00\x01\x02\xff\xfe\xfd"
        blob = encrypt(data, KEY)
        path = tmp_path / "bin.dat"
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            decrypt_to_fd(blob, KEY, fd)
        finally:
            os.close(fd)
        assert path.read_bytes() == data

    def test_blob_is_base64_string(self):
        blob = encrypt("x = 1", KEY)
        assert isinstance(blob, str)
        base64.b64decode(blob)


class TestHMACVerification:
    def test_wrong_key_raises(self):
        blob = encrypt("secret", KEY)
        with pytest.raises(ValueError, match="HMAC verification failed"):
            decrypt_to_code(blob, WRONG_KEY, "<test>")

    def test_wrong_key_fd_raises(self, tmp_path):
        blob = encrypt(b"\x00\x01", KEY)
        path = tmp_path / "wrong.bin"
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            with pytest.raises(ValueError, match="HMAC verification failed"):
                decrypt_to_fd(blob, WRONG_KEY, fd)
        finally:
            os.close(fd)

    def test_tampered_ciphertext_raises(self):
        blob = encrypt("secret", KEY)
        raw = bytearray(base64.b64decode(blob))
        raw[20] ^= 0xFF
        tampered = base64.b64encode(bytes(raw)).decode()
        with pytest.raises(ValueError, match="HMAC verification failed"):
            decrypt_to_code(tampered, KEY, "<test>")

    def test_truncated_blob_raises(self):
        blob = encrypt("secret", KEY)
        raw = base64.b64decode(blob)
        short = base64.b64encode(raw[:10]).decode()
        with pytest.raises((ValueError, Exception)):
            decrypt_to_code(short, KEY, "<test>")


class TestKeyValidation:
    def test_short_key_raises(self):
        with pytest.raises(ValueError, match="32 bytes"):
            encrypt("x", b"short")

    def test_long_key_raises(self):
        with pytest.raises(ValueError, match="32 bytes"):
            encrypt("x", b"x" * 64)

    def test_non_bytes_key_raises(self):
        with pytest.raises(TypeError, match="bytes"):
            encrypt("x", "not bytes")


class TestScrub:
    def test_scrub_bytearray(self):
        data = bytearray(b"sensitive data")
        scrub(data)
        assert all(b == 0 for b in data)

    def test_scrub_bytes_rejected(self):
        with pytest.raises(TypeError):
            scrub(b"sensitive")

    def test_scrub_str_rejected(self):
        with pytest.raises(TypeError):
            scrub("sensitive string")


class TestCExtension:
    def test_c_extension_available(self):
        import _paker_crypto
        # Runtime API.
        assert hasattr(_paker_crypto, "compile_encrypted")
        assert hasattr(_paker_crypto, "decrypt_and_unmarshal")
        assert hasattr(_paker_crypto, "decrypt_to_fd")
        assert hasattr(_paker_crypto, "decrypt_into_bytearray")
        assert hasattr(_paker_crypto, "scrub")
        # Packer-only primitive.
        assert hasattr(_paker_crypto, "encrypt_for_pack")

    def test_runtime_api_does_not_expose_decrypt_bytes(self):
        """Regression guard: the previous `decrypt_bytes(blob, key) -> bytes`
        function was removed because it enabled a SourceRestorer-class attack."""
        import _paker_crypto
        assert not hasattr(_paker_crypto, "decrypt_bytes")

    def test_compile_encrypted_returns_code(self):
        import _paker_crypto
        nonce = os.urandom(16)
        # Use encrypt_for_pack to produce a valid ciphertext.
        enc_key, _ = _derive(KEY)
        source = b"result = 99\n"
        ciphertext = _paker_crypto.encrypt_for_pack(source, enc_key, nonce)
        blob = nonce + ciphertext
        code_obj = _paker_crypto.compile_encrypted(blob, enc_key, "<cext>")
        assert type(code_obj).__name__ == "code"
        ns = {}
        exec(code_obj, ns)
        assert ns["result"] == 99

    def test_decrypt_to_fd_writes_plaintext(self, tmp_path):
        import _paker_crypto
        nonce = os.urandom(16)
        enc_key, _ = _derive(KEY)
        data = b"\xDE\xAD\xBE\xEF"
        ciphertext = _paker_crypto.encrypt_for_pack(data, enc_key, nonce)
        blob = nonce + ciphertext

        path = tmp_path / "decrypted.bin"
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            _paker_crypto.decrypt_to_fd(blob, enc_key, fd)
        finally:
            os.close(fd)
        assert path.read_bytes() == data

    def test_decrypt_into_bytearray_writes_plaintext(self):
        import _paker_crypto
        nonce = os.urandom(16)
        enc_key, _ = _derive(KEY)
        data = b"\xDE\xAD\xBE\xEF"
        ciphertext = _paker_crypto.encrypt_for_pack(data, enc_key, nonce)
        blob = nonce + ciphertext

        dest = bytearray()
        _paker_crypto.decrypt_into_bytearray(blob, enc_key, dest)
        assert bytes(dest) == data

    def test_invalid_key_length(self):
        import _paker_crypto
        with pytest.raises(ValueError):
            _paker_crypto.compile_encrypted(b"\x00" * 20, b"short", "<test>")

    def test_blob_too_short(self):
        import _paker_crypto
        with pytest.raises(ValueError):
            _paker_crypto.compile_encrypted(b"\x00" * 10, KEY, "<test>")


def _derive(master_key):
    from paker._crypto import _derive_keys
    return _derive_keys(master_key)


class TestEncryptedPakerPipeline:
    def test_encrypted_simple_module(self):
        data = {
            "enc_mod": {
                "type": "module", "extension": "py",
                "code": "VALUE = 'encrypted_hello'\n",
            }
        }
        dumped = json.loads(json.dumps(
            {"enc_mod": paker._dump(
                type(sys)("enc_mod"),
                [], False, None,
            )} if False else data
        ))

        from paker._crypto import encrypt as enc
        dumped["enc_mod"]["code"] = enc(dumped["enc_mod"]["code"], KEY)
        dumped["enc_mod"]["encrypted"] = True

        with paker.loads(dumped, key=KEY) as imp:
            import enc_mod
            assert enc_mod.VALUE == "encrypted_hello"

    def test_encrypted_dumps_loads_roundtrip(self):
        sys.path.insert(0, TESTS_DATA)
        try:
            dumped = paker.dumps("testpackage", key=KEY)
            serialized = json.dumps(dumped)

            pkg = dumped["testpackage"]
            assert pkg.get("encrypted") is True
            assert "def " not in pkg["code"]
            assert "Fibonacci" not in pkg["code"]

            for name in list(sys.modules):
                if name.startswith("testpackage"):
                    del sys.modules[name]

            with paker.loads(serialized, key=KEY) as imp:
                import testpackage
                assert testpackage.Fibonacci.fib(5) == [0, 1, 1, 2, 3]
                assert testpackage.Fibonacci.fib_odd(5) == [1, 1, 3]
        finally:
            sys.path.pop(0)

    def test_encrypted_wrong_key_fails(self):
        sys.path.insert(0, TESTS_DATA)
        try:
            dumped = paker.dumps("testpackage", key=KEY)
            serialized = json.dumps(dumped)

            for name in list(sys.modules):
                if name.startswith("testpackage"):
                    del sys.modules[name]

            imp = paker.loads(serialized, key=WRONG_KEY)
            with pytest.raises(ValueError, match="HMAC"):
                imp.load_module("testpackage")
            imp.unload()
        finally:
            sys.path.pop(0)

    def test_no_key_plaintext_still_works(self):
        data = {
            "plain_mod": {
                "type": "module", "extension": "py",
                "code": "X = 123\n",
            }
        }
        with paker.loads(data) as imp:
            import plain_mod
            assert plain_mod.X == 123

    def test_scrub_after_import(self):
        from paker._crypto import encrypt as enc
        data = {
            "scrub_test": {
                "type": "module", "extension": "py",
                "encrypted": True,
                "code": enc("SCRUBBED = True\n", KEY),
            }
        }
        from paker.importers import JsonImporter
        imp = JsonImporter(data, key=KEY)
        import scrub_test
        assert scrub_test.SCRUBBED is True
        assert data["scrub_test"]["code"] is None
        imp.unload()

    def test_encrypted_pyc_module(self):
        source = "COMPILED = 42\n"
        code_obj = compile(source, "<test>", "exec", optimize=2)
        raw_pyc = base64.b64encode(marshal.dumps(code_obj)).decode()

        from paker._crypto import encrypt as enc
        data = {
            "enc_pyc": {
                "type": "module", "extension": "pyc",
                "encrypted": True,
                "code": enc(base64.b64decode(raw_pyc), KEY),
            }
        }
        from paker.importers import JsonImporter
        imp = JsonImporter(data, key=KEY)
        import enc_pyc
        assert enc_pyc.COMPILED == 42
        imp.unload()


def _encrypt_module_dict(module_dict: dict, key: bytes) -> dict:
    from paker._crypto import encrypt as enc
    result = {}
    for name, node in module_dict.items():
        encrypted_node = dict(node)
        encrypted_node["code"] = enc(node["code"], key)
        encrypted_node["encrypted"] = True
        if "modules" in node:
            encrypted_node["modules"] = _encrypt_module_dict(node["modules"], key)
        result[name] = encrypted_node
    return result


class TestKeyMismatchOnReuse:
    def test_two_loads_different_keys(self):
        key_a = os.urandom(32)
        key_b = os.urandom(32)

        enc_a = _encrypt_module_dict(
            {"kmod_a": {"type": "module", "extension": "py", "code": "A = 1\n"}},
            key_a,
        )
        enc_b = _encrypt_module_dict(
            {"kmod_b": {"type": "module", "extension": "py", "code": "B = 2\n"}},
            key_b,
        )

        imp_a = paker.loads(enc_a, key=key_a)
        try:
            import kmod_a
            assert kmod_a.A == 1

            imp_b = paker.loads(enc_b, key=key_b)
            try:
                import kmod_b
                assert kmod_b.B == 2
            finally:
                imp_b.unload()
        finally:
            imp_a.unload()

    def test_same_key_reuses_importer(self):
        key = os.urandom(32)

        enc_a = _encrypt_module_dict(
            {"kreuse_a": {"type": "module", "extension": "py", "code": "X = 10\n"}},
            key,
        )
        enc_b = _encrypt_module_dict(
            {"kreuse_b": {"type": "module", "extension": "py", "code": "Y = 20\n"}},
            key,
        )

        imp = paker.loads(enc_a, key=key)
        imp2 = paker.loads(enc_b, key=key)
        assert imp is imp2

        try:
            import kreuse_a
            import kreuse_b
            assert kreuse_a.X == 10
            assert kreuse_b.Y == 20
        finally:
            imp.unload()

    def test_no_key_then_key_creates_new_importer(self):
        key = os.urandom(32)

        plain = {"kplain": {"type": "module", "extension": "py", "code": "P = 1\n"}}
        enc = _encrypt_module_dict(
            {"kenc": {"type": "module", "extension": "py", "code": "E = 2\n"}},
            key,
        )

        imp1 = paker.loads(plain)
        imp2 = paker.loads(enc, key=key)
        assert imp1 is not imp2

        try:
            import kplain
            assert kplain.P == 1
        finally:
            imp1.unload()

        try:
            import kenc
            assert kenc.E == 2
        finally:
            imp2.unload()


class TestHKDFKeyDerivation:
    def test_different_derived_keys(self):
        from paker._crypto import _derive_keys
        key = os.urandom(32)
        enc_key, mac_key = _derive_keys(key)
        assert enc_key != mac_key
        assert len(enc_key) == 32
        assert len(mac_key) == 32

    def test_deterministic(self):
        from paker._crypto import _derive_keys
        key = os.urandom(32)
        assert _derive_keys(key) == _derive_keys(key)

    def test_different_master_different_derived(self):
        from paker._crypto import _derive_keys
        k1 = os.urandom(32)
        k2 = os.urandom(32)
        assert _derive_keys(k1) != _derive_keys(k2)


class TestTamperingDetection:
    """Regression guard for the SourceRestorer-class monkey-patch attack.

    The attack: import paker, replace `_paker_crypto.decrypt_*` with a Python
    shim that logs plaintext before calling the real function, then run the
    target's decrypt flow. Our defense: check that the attribute resolution
    returns a C builtin on every decrypt call.
    """

    def test_clean_decrypt_succeeds(self):
        """Baseline: with no tampering, a standard decrypt completes."""
        blob = encrypt("x = 42\n", KEY)
        code = decrypt_to_code(blob, KEY, "<clean>")
        ns = {}
        exec(code, ns)
        assert ns["x"] == 42

    def test_python_wrapper_replacement_blocks_decrypt(self):
        """Wrapping a C entry point with a Python function trips the check."""
        import _paker_crypto
        from paker._crypto import PakerTamperingError

        original = _paker_crypto.compile_encrypted

        def spy(blob, key, filename):
            return original(blob, key, filename)

        _paker_crypto.compile_encrypted = spy
        try:
            blob = encrypt("x = 1", KEY)
            with pytest.raises(PakerTamperingError, match="compile_encrypted"):
                decrypt_to_code(blob, KEY, "<patched>")
        finally:
            _paker_crypto.compile_encrypted = original

    def test_entry_point_removal_blocks_decrypt(self, tmp_path):
        """Deleting a C entry point trips the check on any sibling call."""
        import _paker_crypto
        from paker._crypto import PakerTamperingError

        original_fd = _paker_crypto.decrypt_to_fd
        del _paker_crypto.decrypt_to_fd
        try:
            blob = encrypt("x = 2", KEY)
            # compile_encrypted checks the whole table; missing decrypt_to_fd
            # trips the "missing" branch.
            with pytest.raises(PakerTamperingError, match="decrypt_to_fd"):
                decrypt_to_code(blob, KEY, "<missing-sibling>")
        finally:
            _paker_crypto.decrypt_to_fd = original_fd

    def test_lambda_replacement_blocks_decrypt(self):
        import _paker_crypto
        from paker._crypto import PakerTamperingError

        original = _paker_crypto.compile_encrypted
        _paker_crypto.compile_encrypted = lambda *a, **kw: None
        try:
            blob = encrypt("x = 1", KEY)
            with pytest.raises(PakerTamperingError, match="compile_encrypted"):
                decrypt_to_code(blob, KEY, "<lambda>")
        finally:
            _paker_crypto.compile_encrypted = original

    def test_sourcerestorer_style_monkey_patch_blocked(self, tmp_path):
        """End-to-end: a SourceRestorer clone wraps decrypt_to_fd and tries
        to exfiltrate plaintext. The C-side check catches it before any
        bytes reach the attacker's wrapper."""
        import _paker_crypto
        from paker._crypto import PakerTamperingError

        captured = []
        original = _paker_crypto.decrypt_to_fd

        def exfiltrator(blob, key, fd):
            original(blob, key, fd)
            captured.append(("captured", blob, key, fd))

        _paker_crypto.decrypt_to_fd = exfiltrator
        try:
            blob = encrypt("confidential = 42\n", KEY)
            path = tmp_path / "out.bin"
            fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            try:
                with pytest.raises(PakerTamperingError):
                    decrypt_to_fd(blob, KEY, fd)
            finally:
                os.close(fd)
            assert captured == []
        finally:
            _paker_crypto.decrypt_to_fd = original

    def test_exception_class_is_exported_by_c_extension(self):
        """PakerTamperingError is defined in C and re-exported from paker._crypto."""
        import _paker_crypto
        from paker._crypto import PakerTamperingError
        assert PakerTamperingError is _paker_crypto.PakerTamperingError
        assert issubclass(PakerTamperingError, RuntimeError)

    def test_overwriting_python_side_check_cannot_bypass(self):
        """Previous paker revisions kept a ``_verify_c_ext_integrity`` helper
        in Python that could be overwritten to a no-op. The current design
        is invulnerable: there is no Python-side check to patch, and the
        C-side check runs unconditionally inside each decrypt call."""
        import paker._crypto as crypto_mod

        # No such attribute exists — the check lives entirely in C.
        assert not hasattr(crypto_mod, "_verify_c_ext_integrity")

        # An attempt to disable by attribute rebinding therefore cannot
        # reach the check; the tamper test still fires on actual patching.
        import _paker_crypto
        original = _paker_crypto.compile_encrypted
        _paker_crypto.compile_encrypted = lambda *a, **kw: None
        try:
            from paker._crypto import PakerTamperingError
            blob = encrypt("x = 1", KEY)
            with pytest.raises(PakerTamperingError):
                decrypt_to_code(blob, KEY, "<sealed>")
        finally:
            _paker_crypto.compile_encrypted = original
