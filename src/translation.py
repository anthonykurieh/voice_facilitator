"""Lightweight translation helper using OpenAI chat."""
import json
import re
from typing import Optional


LANGUAGE_NAME = {
    "en": "English",
    "ar": "Arabic",
}


class Translator:
    def __init__(self, client, model: str):
        self.client = client
        self.model = model

    def detect_language(self, text: str) -> str:
        if not text or not text.strip():
            return "en"
        # Fast-path for Arabic to avoid extra calls
        if re.search(r"[\u0600-\u06FF]", text):
            return "ar"
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Detect the language of the user's text. "
                            "Return JSON: {\"language\":\"<ISO 639-1 code>\"}. "
                            "Use \"en\" for English. If mixed, return the dominant language."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.choices[0].message.content)
            lang = str(payload.get("language", "en")).lower().strip()
            return lang or "en"
        except Exception:
            return "en"

    def translate(self, text: str, target_language: str) -> str:
        if not text or not text.strip():
            return text
        target_name = LANGUAGE_NAME.get(target_language, target_language)
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"Translate the user's text into {target_name}. "
                            "Preserve names, phone numbers, dates, times (HH:MM), emails, and IDs. "
                            "Do not add explanations or quotes."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        except Exception:
            return text
