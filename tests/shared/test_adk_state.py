# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-22ed5d7954437fdff8e64a7f


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from shared.adk_state import derive_reply_target_from_owner_key, normalize_reply_target

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-22ed5d7954437fdff8e64a7f"
# from __debug_provenance_i__ import or


def test_telegram_user_reply_target_is_saved_messages_only():
    assert normalize_reply_target(
        {
            "transport": "telegram_user",
            "to": "synthetic-recipient",
            "chat_id": 12345,
        }
    ) == {"transport": "telegram_user"}
    assert derive_reply_target_from_owner_key("telegram_user:synthetic-owner") == {
        "transport": "telegram_user"
    }
