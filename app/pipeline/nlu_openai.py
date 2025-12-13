from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class NLUResult:
    intent: str
    confidence: float
    entities: Dict[str, Any]