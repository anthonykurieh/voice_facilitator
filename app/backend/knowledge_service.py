from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from app.business.profile import BusinessProfile


@dataclass
class KBResult:
    snippets: List[str]


class KnowledgeService:
    """
    Simple retrieval from FAQ, policies, services, and staff knowledge.

    Works for any service business: salon, clinic, spa, etc.
    """

    def __init__(self, profile: BusinessProfile):
        self.profile = profile
        self.docs: List[Dict[str, Any]] = []

        # FAQ
        for f in profile.faq:
            q = f.get("q", "").strip()
            a = f.get("a", "").strip()
            if not q and not a:
                continue
            self.docs.append({
                "text": f"Q: {q}\nA: {a}",
                "type": "faq",
            })

        # Policies
        for key, val in profile.policies.items():
            if not val:
                continue
            self.docs.append({
                "text": f"Policy ({key}): {val}",
                "type": "policy",
            })

        # Services
        for s in profile.services:
            name = s.get("name", "").strip()
            desc = s.get("description", "").strip()
            self.docs.append({
                "text": f"Service: {name}\nDescription: {desc}",
                "type": "service",
            })

        # Staff
        for s in profile.staff:
            name = s.get("name", "").strip() or "A staff member"
            role = s.get("role", "").strip()
            specs = ", ".join(s.get("specialties", []))
            years = s.get("years_experience", None)

            parts = [name]
            if role:
                parts.append(f"role: {role}")
            if specs:
                parts.append(f"specialties: {specs}")
            if years is not None:
                parts.append(f"experience: {years} years")

            text = "Staff member: " + "; ".join(parts)

            self.docs.append({
                "text": text,
                "type": "staff",
            })

    def query(self, question: str, topic: Optional[str] = None) -> KBResult:
        """
        Simple keyword-overlap scoring (RAG-lite).
        """
        q = question.lower()
        scored: List[tuple[int, str]] = []

        for doc in self.docs:
            text = doc["text"].lower()
            score = 0

            for w in q.split():
                if len(w) > 2 and w in text:
                    score += 1

            if topic and topic.lower() in text:
                score += 2

            if score > 0:
                scored.append((score, doc["text"]))

        scored.sort(key=lambda x: x[0], reverse=True)
        return KBResult(snippets=[d for (_, d) in scored[:3]])