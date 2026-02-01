"""Admin analytics entrypoint (text first, optional STT/TTS reuse).

Usage:
    python analytics_admin.py

Requires DB env vars set (DB_HOST/USER/PASSWORD/NAME) and OPENAI_API_KEY.
"""
import json
import os
import re
import sys
import logging
from datetime import datetime
from typing import List, Dict, Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.analytics_agent import AnalyticsAgent
from src.tts import TextToSpeech
from src.stt import SpeechToText
from src.config import OPENAI_API_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SQL_LOG_PATH = os.path.join(os.path.dirname(__file__), "analytics_sql.log")


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


def _log_sql(question: str, sql: str, status: str):
    try:
        line = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "status": status,
            "question": question,
            "sql": sql,
        }
        with open(SQL_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"Failed to log SQL: {e}")


def answer_question(agent: AnalyticsAgent, engine: Engine, question: str, speak: bool = False,
                    tts: TextToSpeech = None) -> Dict[str, Any]:
    plan = agent.generate_sql(question)
    if plan.get("needs_clarification"):
        return {
            "needs_clarification": True,
            "clarification_question": plan.get("clarification_question", "Can you clarify?"),
        }
    if plan.get("error"):
        _log_sql(question, plan.get("sql", ""), "generation_error")
        return {"error": f"SQL generation error: {plan['error']}"}
    sql = plan.get("sql", "")
    _log_sql(question, sql, "generated")
    try:
        rows = safe_execute(engine, sql)
    except Exception as e:
        _log_sql(question, sql, "execution_error")
        return {"error": f"SQL execution error: {e}"}
    meta = {"row_count": len(rows)}
    summary = agent.summarize(question, rows, meta)
    if speak and tts:
        tts.speak(summary)
    return {"summary": summary, "sql": sql}


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
            current_question = question
            for _ in range(2):
                result = answer_question(agent, engine, current_question, speak=use_voice, tts=tts)
                if result.get("needs_clarification"):
                    follow_up = result.get("clarification_question", "Can you clarify?")
                    print(f"Assistant: {follow_up}")
                    if use_voice:
                        tts.speak(follow_up)
                        clar = stt.listen()
                        if not clar.strip():
                            print("(No speech detected)")
                            continue
                        print(f"You (voice): {clar}")
                    else:
                        clar = input("You: ").strip()
                        if not clar:
                            continue
                    current_question = f"{current_question} {clar}"
                    continue
                if result.get("error"):
                    print(f"Answer: {result['error']}\n")
                else:
                    print(f"Answer: {result['summary']}\n")
                    print(f"SQL: {result['sql']}\n")
                break
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
