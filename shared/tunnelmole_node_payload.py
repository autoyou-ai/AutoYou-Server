# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-8bf74404b7e8de1cc68d1c8c

"""Materialize the Tunnelmole Node launcher from a compiled runtime payload."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import base64
import hashlib
import os
import tempfile
import zlib
from pathlib import Path

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-8bf74404b7e8de1cc68d1c8c"


LAUNCHER_FILENAME = "tunnelmole_node_launcher.mjs"
# The canonical launcher source in git uses LF. Regenerate this payload from the
# LF form only (normalize CRLF first) - a checkout with core.autocrlf=true will
# otherwise bake Windows line endings in here and desync it from the repo file
# on every other platform. .gitattributes pins *.mjs to LF to prevent that.
LAUNCHER_SHA256 = "e1f5173f216535a62c7c0c26a269172f20bce6e04143350862f2f99ceef8332c"
_COMPRESSED_LAUNCHER_BASE64 = (
    "eNqlVm1z4jYQ/s6vWDydG3sGnLc2nbuUdmhCWiYEmEDayXQ6nLCXoMSWXEkml8vlv3clGzAc6V2ufDCWtfto9ejZXe3twanMHhW/nRvwowAO9w+PYZChGBmp"
    "8BF6vdMQ2kkCzkSDQo1qgXFY29uDHo9QaIwhFzEqMHOEdm7kjcxhJHMVYbO9YDxh0wSXts5vhDTunnb6ow5w4fwyJe8wMqCkNDCTCpLCnuZplDLDpXC+7e7e"
    "ZQ+MYlxwcQu5dr5zPuWGAnngZi5zAw+KG4MCWE5jxT86f/DLIGB0FL4NCrjr8eBmcN0cXg3+6PTb/dNO86q54KwZ49Hb6PuDH6bRLP7xCI8PDo+Po+P9Wa3G"
    "00wqAxkzc5gpmYInZIzv7Ng7Wc4+ufmxPOcJXl/14LlqmquELGuRFNriRPfsFs+4ghaMjKJd+bSjCLUOUSzCMsLJ+Lrf7/QuB73OpD8460yG7dOL9m+dyVn3"
    "Cj59As8LQnJO/eCkBH7QHRFnkgvzOuA/R5NO/2w46PbHu4HnxmTfBv37eDz8AjjL+AXp7lWw7WF3ctG52Q0Yy5S08jrAs8Flu9vfjeeOtwX9PJ2iCjOmNHaF"
    "8V9zcoOrYvP7XtCAg32CJiUO8ylpHlISCMSKL1C7xNAsRUhkdD8jJTUzLgTJXGQpRAlH4p/d0u4oLJdEDsKCmZzsklRS4tl8Jdk3KHWzhEU2aaxtLjS/tVjG"
    "WU25YOqRyHoQiWRxCF0DAheoLJpGynwmgHiGe3xs0HsMihBs9nKaIy+YSwpUS2Bknsyac6ltQpI5REwIyuoE2X24JNFFemn3+jrRX/9KhWPzYKDVaoF3sM6o"
    "GdUcgvVTQqPMagAqJRXZ/QxPNaCfNaNNh+67//6vNVtNm5/NhOUimqP6G757KkGe39MpWV8+A9/5wZs3YB4zlLMCvwhDTm0Z8+ykpw2ltmc5Wjm4l9BNBGUw"
    "nwdU8lG1LRd/BkxsTVzG8HUYK2/3XNH8gRv/gKaeiTiLWK9UIiK4Xiqc63MqtQZ9K/zAzrgM+KkF+8vlLeG+d8m1tupS+E/OlVXWWoRLRoEOlyspUpJu6NnF"
    "3dIVORBJfqVy0XIb5YbGRYUINhevZk+akwis4iKmSNNVOWKJQ5JVFUVXQqlvxVLfDKa+HU19ZzifcVEN4ut42WwPQ9ttWq6phNSDZbKgA1mdFznUDG11LW9j"
    "nzN+S/vIqQOppHRedSTfQd3RNvzKEg3wYq6NR/+Ff3invSAI5wpnJxVwTbcDcvl2dK0i++fSLUOll58L3BdWXRP3vxfmdGP5sLFMZZ0niHHG8sS8K1mkxGsB"
    "e2DcQNHf/S12gxfJ2XbcZi74rw1uO+8gICgj39LuVmWwJ2nVx7MQ1517Le2TF4y3Wn11uKwp7q+IcmNvoa0ao+KLXyme9U2rclSNt0ihcaWFFTYQ89jltUXm"
    "LOEf0eXKurZt1pIq5s5FKZNSucCuwfSX0PeKTPY2a+2XIKg3Wv+Vd2NZnzb4KY5XZvYaqonIp6KIPq9pKa4p1ZBL63B1gSledvC+LYtw/cEvURp0Zc7RFhaq"
    "iiaabzWQgvJzetr6JGmvTBV3ihcKV3H7CL1lc7UV61+Lkw4c"
)


def launcher_bytes() -> bytes:
    payload = zlib.decompress(base64.b64decode(_COMPRESSED_LAUNCHER_BASE64))
    actual = hashlib.sha256(payload).hexdigest()
    if actual != LAUNCHER_SHA256:
        raise RuntimeError("Embedded Tunnelmole launcher failed its integrity check.")
    return payload


def materialize_launcher(target_dir: str | os.PathLike[str]) -> Path:
    """Atomically create or repair the launcher in a mutable per-user directory."""

    destination_dir = Path(target_dir).expanduser().resolve(strict=False)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / LAUNCHER_FILENAME
    payload = launcher_bytes()

    try:
        if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == LAUNCHER_SHA256:
            return destination
    except OSError:
        pass

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{LAUNCHER_FILENAME}.", dir=destination_dir)
    # from __debug_provenance_s__ import btc
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
        try:
            destination.chmod(0o600)
        except OSError:
            pass
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
    return destination
