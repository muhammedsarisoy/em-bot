"""Telegram Bot API, sadece requests ile."""
import logging

import requests

log = logging.getLogger("tg")


class Telegram:
    def __init__(self, token: str, allowed: set[int]):
        self.url = f"https://api.telegram.org/bot{token}"
        self.allowed = allowed

    def send(self, chat_id: int, text: str) -> None:
        try:
            requests.post(f"{self.url}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=15)
        except requests.RequestException as e:
            log.warning("gönderilemedi: %s", e)

    def send_photo(self, chat_id: int, png: bytes, caption: str = "") -> None:
        try:
            requests.post(
                f"{self.url}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": ("grafik.png", png, "image/png")},
                timeout=30,
            )
        except requests.RequestException as e:
            log.warning("fotoğraf gönderilemedi: %s", e)

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """Sohbette '/' yazınca açılan komut menüsü."""
        try:
            requests.post(
                f"{self.url}/setMyCommands",
                json={"commands": [{"command": c, "description": d} for c, d in commands]},
                timeout=15,
            )
        except requests.RequestException as e:
            log.warning("komut menüsü ayarlanamadı: %s", e)

    def broadcast(self, text: str) -> None:
        for chat_id in self.allowed:
            self.send(chat_id, text)

    def updates(self, offset: int, timeout: int = 50) -> list[dict]:
        r = requests.get(
            f"{self.url}/getUpdates",
            params={"offset": offset, "timeout": timeout, "allowed_updates": '["message"]'},
            timeout=timeout + 10,
        )
        r.raise_for_status()
        return r.json().get("result", [])
