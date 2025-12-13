from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
import uuid


@dataclass
class Message:
    role: str               # "user" or "assistant"
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class SessionState:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: List[Message] = field(default_factory=list)

    # Captured from NLU entities
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None

    def add_message(self, role: str, content: str) -> None:
        self.messages.append(Message(role=role, content=content))