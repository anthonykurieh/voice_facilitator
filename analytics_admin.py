"""Admin analytics entrypoint (text first, optional STT/TTS reuse).

Usage:
    python analytics_admin.py

Requires DB env vars set (DB_HOST/USER/PASSWORD/NAME) and OPENAI_API_KEY.
"""
import json
import os
import re
import sys
from typing import List, Dict, Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.analytics_agent import AnalyticsAgent
from src.tts import TextToSpeech
from src.stt import SpeechToText
from src.config import OPENAI_API_KEY


def get_engine() -> Engine:
    host = os.getenv("DB_HOST") or os.getenv("MYSQL_HOST", "localhost")
    port = os.getenv("DB_PORT") or os.getenv("MYSQL_PORT", "3306")
    user = os.getenv("DB_USER") or os.getenv("MYSQL_USER", "root")
    password = os.getenv("DB_PASSWORD") or os.getenv("MYSQL_PASSWORD", "")
    db = os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "voice_assistant")
    return create_engine(f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}")


def safe_execute(engine: Engine, sql: str) -> List[Dict[str, Any]]:
    sql_lower = sql.lower()
    if not sql_lower.strip().startswith("select"):
        raise ValueError("Only SELECT queries are allowed")
    banned = ["insert", "update", "delete", "alter", "drop", "truncate", "create"]
    if any(b in sql_lower for b in banned):
        raise ValueError("Unsafe SQL detected")
    # Enforce LIMIT if missing
    if "limit" not in sql_lower:
        sql = sql.rstrip("; ") + " LIMIT 200"
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)
        return df.to_dict(orient="records")


def answer_question(agent: AnalyticsAgent, engine: Engine, question: str, speak: bool = False,
                    tts: TextToSpeech = None) -> str:
    plan = agent.generate_sql(question)
    if plan.get("error"):
        return f"SQL generation error: {plan['error']}"
    sql = plan.get("sql", "")
    try:
        rows = safe_execute(engine, sql)
    except Exception as e:
        return f"SQL execution error: {e}"
    meta = {"row_count": len(rows)}
    summary = agent.summarize(question, rows, meta)
    if speak and tts:
        tts.speak(summary)
    return summary


def interactive_loop(use_voice: bool = False):
    agent = AnalyticsAgent()
    engine = get_engine()
    tts = TextToSpeech(OPENAI_API_KEY)
    stt = SpeechToText(OPENAI_API_KEY)

    print("\nAdmin Analytics. Ask a question (type or speak). Ctrl+C to exit.\n")
    while True:
        try:
            if use_voice:
                question = stt.listen()
                if not question.strip():
                    print("(No speech detected)")
                    continue
                print(f"You (voice): {question}")
            else:
                question = input("You: ").strip()
                if not question:
                    continue
            summary = answer_question(agent, engine, question, speak=use_voice, tts=tts)
            print(f"Answer: {summary}\n")
        except KeyboardInterrupt:
            print("\nGoodbye")
            break
        except Exception as e:
            print(f"Error: {e}")


def main():
    use_voice = False
    if len(sys.argv) > 1 and sys.argv[1] == "--voice":
        use_voice = True
    interactive_loop(use_voice=use_voice)


if __name__ == "__main__":
    main()
