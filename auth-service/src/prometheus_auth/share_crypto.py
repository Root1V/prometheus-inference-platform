# See memory/specs/016-credential-share-link.md — Encryption scheme
#
# AES-256-GCM: authenticated encryption — the GCM tag detects any tampering.
# Layout of stored bytes: IV (12 B) || ciphertext+tag (N+16 B)
# Everything is base64-encoded for safe storage in the TEXT column.
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def encrypt_secret(key_hex: str, plaintext: str) -> str:
    """Encrypt *plaintext* with AES-256-GCM using *key_hex* (64 hex chars).

    Returns a base64-encoded string: base64(iv || ciphertext || tag).
    """
    key = bytes.fromhex(key_hex)
    iv = os.urandom(12)  # 96-bit GCM nonce; unique per call
    aesgcm = AESGCM(key)
    # AESGCM.encrypt() returns ciphertext + 16-byte GCM tag concatenated
    ct_with_tag = aesgcm.encrypt(iv, plaintext.encode(), None)
    return base64.b64encode(iv + ct_with_tag).decode()


def decrypt_secret(key_hex: str, ciphertext_b64: str) -> str:
    """Decrypt AES-256-GCM ciphertext stored as base64.

    Raises ``ValueError`` on GCM tag mismatch (tampering or wrong key).
    Raises ``ValueError`` if the blob is too short to contain a nonce.
    """
    key = bytes.fromhex(key_hex)
    raw = base64.b64decode(ciphertext_b64)
    if len(raw) < 13:  # 12 B IV + at least 1 B ciphertext
        raise ValueError("Ciphertext blob is too short to be valid.")
    iv, ct_with_tag = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(iv, ct_with_tag, None).decode()
    except Exception as exc:
        raise ValueError("Decryption failed — ciphertext tampered or wrong key.") from exc
