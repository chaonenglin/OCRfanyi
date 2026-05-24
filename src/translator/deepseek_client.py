import aiohttp
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

SYSTEM_PROMPT = """You are a precise English-to-Chinese translator.
- Translate each line of English text to Simplified Chinese.
- Return ONLY the Chinese translation, one line per input line.
- Input lines are separated by "|||". Output translations separated by "|||" in the exact same order.
- Do NOT add explanations, notes, or any extra text.
- If a line is not English or cannot be translated, keep it unchanged."""


class DeepSeekClient:
    def __init__(self):
        self.api_key = DEEPSEEK_API_KEY
        self.base_url = DEEPSEEK_BASE_URL.rstrip("/")
        self.model = DEEPSEEK_MODEL
        self._session = None

    async def _get_session(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []

        joined = "|||".join(texts)
        session = await self._get_session()
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": joined},
            ],
            "temperature": 0.0,
            "max_tokens": 2000,
        }

        async with session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()

        if "choices" not in data:
            raise RuntimeError(f"DeepSeek API error: {data}")

        content = data["choices"][0]["message"]["content"].strip()
        translations = [t.strip() for t in content.split("|||")]

        while len(translations) < len(texts):
            translations.append(texts[len(translations)])
        return translations[: len(texts)]

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
