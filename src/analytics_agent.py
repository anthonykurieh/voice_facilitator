"""LLM-powered analytics agent: NL -> SQL -> natural summary."""
import json
from typing import Any, Dict, List
from openai import OpenAI
from pathlib import Path
from src.config import OPENAI_API_KEY, DIALOG_MODEL

SCHEMA_PATH = Path("schema_docs/analytics_schema.md")


def _load_schema() -> str:
    if SCHEMA_PATH.exists():
        return SCHEMA_PATH.read_text()
    return ""


class AnalyticsAgent:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.schema = _load_schema()

    def generate_sql(self, question: str) -> Dict[str, Any]:
        """Generate SELECT SQL for the given question.

        Returns a dict with keys: sql, reasoning. Errors return an error field.
        """
        system_prompt = f"""
You convert business questions to safe, read-only SQL.
Rules:
- SELECT only. Absolutely no INSERT/UPDATE/DELETE/ALTER/DROP/TRUNCATE/CREATE.
- Use only these tables/columns:\n{self.schema}
- Limit row outputs with LIMIT 200 when listing.
- Return JSON: {{"sql": "...", "reasoning": "..."}}
- If unsure, default to counting (COUNT(*)) rather than returning raw rows.
"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question}
        ]
        try:
            resp = self.client.chat.completions.create(
                model=DIALOG_MODEL,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"}
            )
            payload = json.loads(resp.choices[0].message.content)
            sql = payload.get("sql", "")
            if not sql.lower().strip().startswith("select"):
                return {"error": "Generated SQL is not SELECT", "sql": sql}
            banned = ["insert", "update", "delete", "alter", "drop", "truncate", "create"]
            if any(word in sql.lower() for word in banned):
                return {"error": "Unsafe SQL detected", "sql": sql}
            return payload
        except Exception as e:
            return {"error": str(e)}

    def summarize(self, question: str, rows: List[Dict[str, Any]], meta: Dict[str, Any]) -> str:
        """Summarize SQL result for voice/text output."""
        # Redact phone/email
        safe_rows = []
        for r in rows[:200]:
            safe = {}
            for k, v in r.items():
                if v is None:
                    safe[k] = v
                elif isinstance(v, str) and ("@" in v or k.lower() in ("phone", "email")):
                    safe[k] = "[redacted]"
                else:
                    safe[k] = v
            safe_rows.append(safe)
        system_prompt = """
You summarize analytics query results concisely for an admin. Respond in plain text, number-first if applicable. Mention totals/percentages briefly. Avoid PII; if data was redacted, don't guess it.
"""
        user_content = json.dumps({"question": question, "rows": safe_rows, "meta": meta})
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
        try:
            resp = self.client.chat.completions.create(
                model=DIALOG_MODEL,
                messages=messages,
                temperature=0.2
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            return f"Error summarizing results: {e}"
