from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Contact:
    email: str
    first_name: str = ""
    last_name: str = ""
    mobile: str = ""
    street: str = ""
    city: str = ""
    zip_code: str = ""
    unsubscribed: bool = False
    last_update: str = ""
    source: str = ""
    baserow_row_id: Optional[int] = None


@dataclass
class Unit:
    name: str
    baserow_row_id: Optional[int] = None


@dataclass
class Position:
    name: str
    baserow_row_id: Optional[int] = None


@dataclass
class Assignment:
    contact_email: str
    unit_name: str
    position_name: str
    source: str = ""
    direct_contact_leader: bool = False
    trained: bool = False
    registration_expiration: str = ""
    last_update: str = ""
    baserow_row_id: Optional[int] = None


@dataclass
class ImportRow:
    raw: dict
    mapped: dict
    contact: Optional[Contact] = None
    unit: str = ""
    position: str = ""


@dataclass
class DiffResult:
    row: ImportRow
    status: str  # NEW | CLEAN_UPDATE | NEW_ADDITIONAL_POSITION | STALE | NO_CHANGE
    existing_contact: Optional[Contact] = None
    existing_assignments: list = field(default_factory=list)
    field_changes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "mapped": self.row.mapped,
            "raw": self.row.raw,
            "existing_contact": {
                "email": self.existing_contact.email,
                "first_name": self.existing_contact.first_name,
                "last_name": self.existing_contact.last_name,
                "mobile": self.existing_contact.mobile,
                "street": self.existing_contact.street,
                "city": self.existing_contact.city,
                "zip_code": self.existing_contact.zip_code,
                "unsubscribed": self.existing_contact.unsubscribed,
                "last_update": self.existing_contact.last_update,
                "source": self.existing_contact.source,
                "baserow_row_id": self.existing_contact.baserow_row_id,
            } if self.existing_contact else None,
            "existing_assignments": [
                {
                    "unit_name": a.unit_name,
                    "position_name": a.position_name,
                    "baserow_row_id": a.baserow_row_id,
                }
                for a in self.existing_assignments
            ],
            "field_changes": self.field_changes,
        }
