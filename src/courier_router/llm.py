from __future__ import annotations
import json
import httpx

SYSTEM = """Ты разбираешь только российские адреса Санкт-Петербурга и Ленинградской области.
Не придумывай координаты. Верни очищенную строку адреса, пригодную для геокодера.
Учитывай сокращения пр-кт, ул, д, к, корп, лит, кв. Квартиру сохраняй, но не путай с домом.
Если данных недостаточно, не выдумывай населённый пункт."""

def clean_with_openai(api_key: str, model: str, address: str, district: str) -> str:
    schema = {
        "type": "object",
        "properties": {
            "normalized_address": {"type": "string"},
            "ambiguity": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number"},
        },
        "required": ["normalized_address", "ambiguity", "confidence"],
        "additionalProperties": False,
    }
    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Район: {district}\nАдрес: {address}"},
        ],
        "text": {"format": {"type": "json_schema", "name": "parsed_address", "strict": True, "schema": schema}},
    }
    r = httpx.post("https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload, timeout=30)
    r.raise_for_status()
    obj = r.json()
    # Responses API exposes convenience output_text in SDK; REST returns nested content.
    for out in obj.get("output", []):
        for c in out.get("content", []):
            if c.get("type") == "output_text":
                return json.loads(c["text"])["normalized_address"]
    raise ValueError("OpenAI не вернул structured output")

def clean_with_anthropic(api_key: str, model: str, address: str, district: str) -> str:
    tool = {
        "name": "return_parsed_address",
        "description": "Вернуть нормализованный российский адрес",
        "input_schema": {
            "type": "object",
            "properties": {
                "normalized_address": {"type": "string"},
                "ambiguity": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number"},
            },
            "required": ["normalized_address", "ambiguity", "confidence"],
            "additionalProperties": False,
        },
    }
    payload = {
        "model": model,
        "max_tokens": 500,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": f"Район: {district}\nАдрес: {address}"}],
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": "return_parsed_address"},
    }
    r = httpx.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json=payload, timeout=30)
    r.raise_for_status()
    for c in r.json().get("content", []):
        if c.get("type") == "tool_use" and c.get("name") == "return_parsed_address":
            return c["input"]["normalized_address"]
    raise ValueError("Anthropic не вернул tool result")
