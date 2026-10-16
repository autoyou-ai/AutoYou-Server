# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Select the owned native storage processor for the Iroh generation."""

import base64
from cryptography.fernet import Fernet, InvalidToken


def native_api():
    from shared.application_json import _native_codec
    api = _native_codec()
    if api is not None:
        required = ("FernetStreamEncryptor", "FernetStreamDecryptor", "fernet_encrypt_bytes", "fernet_decrypt_bytes",
                    "ProtectedFileWriter", "atomic_write_file", "write_plain_file", "write_sealed_file", "normalise_sqlite_snapshot")
        if any(not callable(getattr(api, name, None)) for name in required):
            from shared.iroh_binding import IrohBindingUnavailable
            raise IrohBindingUnavailable("The owned SDK lacks native protected storage support")
    return api


class _NativeFernet:
    def __init__(self, api, key):
        self._api = api
        self._key = base64.urlsafe_b64decode(key)
        if len(self._key) != 32:
            raise ValueError("Invalid protected storage key")

    def encrypt(self, data):
        return self._api.fernet_encrypt_bytes(self._key, data)

    def decrypt(self, token):
        if isinstance(token, str):
            token = token.encode("ascii")
        try:
            return self._api.fernet_decrypt_bytes(self._key, token)
        except ValueError:
            raise InvalidToken from None

    def write_file(self, path, data, *, sqlite=False):
        self._api.write_sealed_file(str(path), self._key, data, sqlite)


def storage_cipher(key):
    api = native_api()
    return Fernet(key) if api is None else _NativeFernet(api, key)
