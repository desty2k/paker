"""Encryption helpers for paker.

Encrypt-then-MAC (AES-256-CTR + HMAC-SHA256) for module code blobs. HKDF-SHA256
derives independent encryption and MAC subkeys from the user-supplied master key.

Wire format per blob: base64(nonce[16] + ciphertext + hmac[32])

Runtime API:
    encrypt(plaintext, key) -> str
        Package-time only. Not used by the runtime loader.
    decrypt_to_code(blob, key, filename) -> code object
        .py source path — plaintext never enters the Python heap.
    decrypt_to_unmarshalled(blob, key) -> code object
        .pyc marshal path — plaintext decrypted, unmarshalled, zeroed in C.
    decrypt_to_fd(blob, key, fd) -> None
        Native loader path — plaintext written to fd and never visible to Python.
    scrub(bytearray) -> None
        Zero the buffer of a bytearray in place.

No runtime function returns plaintext as a Python ``bytes`` object. Plaintext
either stays inside C (for code-object paths) or lands directly in an fd the
caller owns (for native loaders).
"""

import hashlib
import hmac
import os
import base64

NONCE_SIZE = 16
HMAC_SIZE = 32
KEY_SIZE = 32

_c_ext = None
# Direct function references captured at import time. Using these instead of
# attribute lookup on ``_c_ext`` means replacing ``_paker_crypto.compile_encrypted``
# after import has no effect on what paker actually invokes — the attacker
# sees their replacement called from user code, but paker still reaches the
# real C function, which runs ``check_integrity`` and raises.
_c_compile_encrypted = None
_c_decrypt_and_unmarshal = None
_c_decrypt_to_fd = None
_c_decrypt_into_bytearray = None
_c_scrub = None

try:
    import _paker_crypto
    _c_ext = _paker_crypto
    _c_compile_encrypted = _paker_crypto.compile_encrypted
    _c_decrypt_and_unmarshal = _paker_crypto.decrypt_and_unmarshal
    _c_decrypt_to_fd = _paker_crypto.decrypt_to_fd
    _c_decrypt_into_bytearray = _paker_crypto.decrypt_into_bytearray
    _c_scrub = _paker_crypto.scrub
except ImportError:
    pass


if _c_ext is not None and hasattr(_c_ext, "PakerTamperingError"):
    PakerTamperingError = _c_ext.PakerTamperingError
else:
    class PakerTamperingError(RuntimeError):
        """Raised when the C extension detects its entry points have been replaced.

        The real exception type is defined in ``_paker_crypto``; this Python
        fallback only comes into play when the C extension is unavailable (in
        which case the pure-Python crypto path runs and has no tampering
        check to trip).
        """


def _validate_key(key: bytes) -> None:
    if not isinstance(key, (bytes, bytearray)):
        raise TypeError(f"key must be bytes (got {type(key).__name__})")
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes (got {len(key)})")


def _derive_keys(master_key: bytes) -> tuple[bytes, bytes]:
    """Derive separate encryption and MAC keys from the master key using HKDF-SHA256."""
    enc_key = hmac.new(master_key, b"paker-enc-v1", hashlib.sha256).digest()
    mac_key = hmac.new(master_key, b"paker-mac-v1", hashlib.sha256).digest()
    return enc_key, mac_key


def _compute_hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _verify_and_split(blob_b64: str, mac_key: bytes) -> bytes:
    """Decode, HMAC-verify, and return the nonce+ciphertext payload."""
    raw = base64.b64decode(blob_b64)
    payload, mac = raw[:-HMAC_SIZE], raw[-HMAC_SIZE:]
    expected = _compute_hmac(mac_key, payload)
    if not hmac.compare_digest(mac, expected):
        raise ValueError("HMAC verification failed — wrong key or tampered data")
    return payload


def encrypt(plaintext: str | bytes, key: bytes) -> str:
    """Encrypt plaintext with AES-256-CTR + HMAC-SHA256.

    Returns a base64-encoded string suitable for JSON serialization. Package-time
    use only — do not expose this path in a deployed runtime.
    """
    _validate_key(key)
    enc_key, mac_key = _derive_keys(key)

    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")

    nonce = os.urandom(NONCE_SIZE)

    if _c_ext is not None:
        # encrypt is packer-side only (build tool) — attribute lookup is fine.
        ciphertext = _c_ext.encrypt_for_pack(plaintext, enc_key, nonce)
    else:
        ciphertext = _aes_ctr_fallback(enc_key, nonce, plaintext)

    payload = nonce + ciphertext
    mac = _compute_hmac(mac_key, payload)
    return base64.b64encode(payload + mac).decode("ascii")


def decrypt_to_code(blob_b64: str, key: bytes, filename: str) -> object:
    """Decrypt and compile Python source to a code object.

    When the C extension is available, plaintext never touches the Python heap.
    """
    _validate_key(key)
    enc_key, mac_key = _derive_keys(key)
    payload = _verify_and_split(blob_b64, mac_key)

    if _c_compile_encrypted is not None:
        return _c_compile_encrypted(payload, enc_key, filename)

    plaintext = bytearray(
        _aes_ctr_fallback(enc_key, payload[:NONCE_SIZE], payload[NONCE_SIZE:])
    )
    try:
        return compile(plaintext.decode("utf-8"), filename, "exec")
    finally:
        scrub(plaintext)


def decrypt_to_unmarshalled(blob_b64: str, key: bytes) -> object:
    """Decrypt a marshalled .pyc bytecode blob to a code object.

    With the C extension, decryption and marshal-read happen in a single C
    call — plaintext is never reachable from Python.
    """
    _validate_key(key)
    enc_key, mac_key = _derive_keys(key)
    payload = _verify_and_split(blob_b64, mac_key)

    if _c_decrypt_and_unmarshal is not None:
        return _c_decrypt_and_unmarshal(payload, enc_key)

    import marshal
    plaintext = bytearray(
        _aes_ctr_fallback(enc_key, payload[:NONCE_SIZE], payload[NONCE_SIZE:])
    )
    try:
        return marshal.loads(bytes(plaintext))
    finally:
        scrub(plaintext)


def decrypt_to_fd(blob_b64: str, key: bytes, fd: int) -> None:
    """Decrypt and write plaintext to the given file descriptor.

    Caller owns `fd` — typically obtained from `os.memfd_create` (Linux) or
    `tempfile.mkstemp` (macOS fallback). Plaintext never crosses into Python.
    """
    _validate_key(key)
    enc_key, mac_key = _derive_keys(key)
    payload = _verify_and_split(blob_b64, mac_key)

    if _c_decrypt_to_fd is not None:
        _c_decrypt_to_fd(payload, enc_key, fd)
        return

    plaintext = bytearray(
        _aes_ctr_fallback(enc_key, payload[:NONCE_SIZE], payload[NONCE_SIZE:])
    )
    try:
        os.write(fd, bytes(plaintext))
    finally:
        scrub(plaintext)


def scrub(obj) -> None:
    """Zero the internal buffer of a bytearray object."""
    if _c_scrub is not None:
        _c_scrub(obj)
    elif isinstance(obj, bytearray):
        for i in range(len(obj)):
            obj[i] = 0


def _aes_ctr_fallback(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """Pure-Python AES-CTR fallback when the C extension is not compiled."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        raise ImportError(
            "paker's C extension (_paker_crypto) is not available and the "
            "pure-Python fallback requires the 'cryptography' package. "
            "Install it with: pip install paker[crypto]"
        ) from None
    cipher = Cipher(algorithms.AES(key), modes.CTR(nonce))
    decryptor = cipher.decryptor()
    return decryptor.update(data) + decryptor.finalize()
