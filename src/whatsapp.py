from __future__ import annotations

import os
from dataclasses import dataclass

from twilio.rest import Client


@dataclass
class WhatsAppNotifier:
    account_sid: str
    auth_token: str
    from_whatsapp: str
    to_whatsapp: str

    @classmethod
    def from_env(cls) -> "WhatsAppNotifier | None":
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        from_whatsapp = os.getenv("TWILIO_WHATSAPP_FROM")
        to_whatsapp = os.getenv("TWILIO_WHATSAPP_TO")

        if not all([account_sid, auth_token, from_whatsapp, to_whatsapp]):
            return None

        return cls(
            account_sid=account_sid,
            auth_token=auth_token,
            from_whatsapp=from_whatsapp,
            to_whatsapp=to_whatsapp,
        )

    def send(self, message: str) -> None:
        client = Client(self.account_sid, self.auth_token)
        client.messages.create(body=message, from_=self.from_whatsapp, to=self.to_whatsapp)
