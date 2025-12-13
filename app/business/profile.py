import os
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class BusinessProfile:
    raw: Dict[str, Any]

    @property
    def id(self) -> str:
        return self.raw.get("business", {}).get("id", "unknown-business")

    @property
    def name(self) -> str:
        return self.raw.get("business", {}).get("name", "Our Business")

    @property
    def type(self) -> str:
        # e.g. "barbershop", "clinic", "spa", "restaurant"
        return self.raw.get("business", {}).get("type", "service_business")

    @property
    def description(self) -> str:
        return self.raw.get("business", {}).get("description", "")

    @property
    def languages(self) -> List[str]:
        return self.raw.get("business", {}).get("languages", [])

    @property
    def contact(self) -> Dict[str, Any]:
        return self.raw.get("contact", {})

    @property
    def opening_hours(self) -> Dict[str, str]:
        return self.raw.get("opening_hours", {})

    @property
    def services(self) -> List[Dict[str, Any]]:
        return self.raw.get("services", [])

    @property
    def staff(self) -> List[Dict[str, Any]]:
        # list of staff members with role, specialties, etc.
        return self.raw.get("staff", [])

    @property
    def policies(self) -> Dict[str, str]:
        return self.raw.get("policies", {})

    @property
    def faq(self) -> List[Dict[str, str]]:
        return self.raw.get("faq", [])

    @property
    def kb_files(self) -> List[str]:
        return self.raw.get("kb_files", [])

    def to_prompt_string(self) -> str:
        """
        Business summary injected directly into the system prompt.
        Generic: works for any service-type business.
        """

        addr = self.contact.get("address", "unspecified address")
        phone = self.contact.get("phone", "unspecified phone")
        website = self.contact.get("website", "")

        # Services summary
        service_names = [s.get("name", "") for s in self.services[:5] if s.get("name")]
        services_str = ", ".join(service_names) if service_names else "a range of services"

        # Staff summary
        staff = self.staff
        staff_count = len(staff)
        staff_phrases: List[str] = []
        for person in staff[:3]:
            name = person.get("name", "A staff member")
            role = person.get("role", "").strip()
            specs = person.get("specialties", [])
            spec_str = ", ".join(specs[:3]) if specs else ""
            if role and spec_str:
                staff_phrases.append(f"{name} ({role}) specializes in {spec_str}")
            elif role:
                staff_phrases.append(f"{name} works as {role}")
            elif spec_str:
                staff_phrases.append(f"{name} specializes in {spec_str}")
            else:
                staff_phrases.append(f"{name} is part of the team")
        staff_summary = "; ".join(staff_phrases)

        # Policies summary
        policy_snippets = []
        for key, val in self.policies.items():
            if not val:
                continue
            policy_snippets.append(f"{key}: {val}")
        policies_short = " ".join(policy_snippets[:3])

        lines = [
            f"You are the phone assistant for '{self.name}', a {self.type}.",
            f"The business is located at: {addr}.",
            f"Main contact phone: {phone}.",
        ]

        if website:
            lines.append(f"Website: {website}.")
        if self.description:
            lines.append(f"Business description: {self.description}")
        lines.append(f"Key services include: {services_str}.")

        if staff_count > 0:
            lines.append(f"There are {staff_count} staff members. {staff_summary}.")

        if policies_short:
            lines.append(f"Key policies: {policies_short}")

        return "\n".join(lines)


def load_business_profile(path: Optional[str] = None) -> BusinessProfile:
    """
    Loads JSON business profile from config/business_profile.json by default.
    Change that file only to reconfigure the business.
    """
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(os.path.dirname(here))
        path = os.path.join(root, "config", "business_profile.json")

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    return BusinessProfile(raw=raw)