"""Import personnel contract history from a clean CSV into User + UserTenure.

The server only ingests a clean, canonical CSV with these columns:

    first_name, last_name, email, employer, team, status, start_date, end_date, notes

- dates are ISO `YYYY-MM-DD`; an empty `end_date` means the contract is open;
- `status` is already one of OrgConfig.employment_statuses;
- `email` may be blank (a synthetic `@archive.invalid` address is generated).

Each distinct person becomes one inactive local `User` (kept out of the
AD-backed directory, which lists only active users) and each row becomes one
`UserTenure` — the consecutive-contract history.

The messy Access-archive (.mdb) -> clean-CSV conversion is a separate one-off
script (scripts/clean_personnel_archive.py); the server never parses .mdb.
"""

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select

from not_dot_net.backend.db import AuthMethod, User, session_scope
from not_dot_net.backend.tenure_service import UserTenure

# Imported ex-staff cannot log in: no usable password, inactive account.
NO_LOGIN_PASSWORD = "!imported-archive-no-login"
REQUIRED_COLUMNS = ("first_name", "last_name", "status", "start_date")


@dataclass(frozen=True)
class ContractRecord:
    first_name: str
    last_name: str
    email: str
    employer: str
    team: str
    status: str
    start_date: date
    end_date: date | None
    notes: str


def _clean(row: dict, key: str) -> str:
    return (row.get(key) or "").strip()


def _iso_date(value: str, *, field: str, line: int) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"row {line}: {field} must be ISO YYYY-MM-DD, got {value!r}") from None


def parse_clean_rows(rows: Iterable[dict]) -> list[ContractRecord]:
    """Validate and build ContractRecords from clean CSV rows."""
    records = []
    for line, row in enumerate(rows, start=2):  # line 1 is the header
        missing = [c for c in REQUIRED_COLUMNS if not _clean(row, c)]
        if missing:
            raise ValueError(f"row {line}: missing required column(s): {', '.join(missing)}")
        start = _iso_date(_clean(row, "start_date"), field="start_date", line=line)
        records.append(
            ContractRecord(
                first_name=_clean(row, "first_name"),
                last_name=_clean(row, "last_name"),
                email=_clean(row, "email").lower(),
                employer=_clean(row, "employer"),
                team=_clean(row, "team"),
                status=_clean(row, "status"),
                start_date=start,
                end_date=_iso_date(_clean(row, "end_date"), field="end_date", line=line),
                notes=_clean(row, "notes"),
            )
        )
    return records


def parse_clean_csv_text(text: str) -> list[ContractRecord]:
    return parse_clean_rows(csv.DictReader(io.StringIO(text)))


def load_clean_csv(path: str) -> list[ContractRecord]:
    with open(path, newline="", encoding="utf-8") as f:
        return parse_clean_rows(csv.DictReader(f))


@dataclass
class ImportSummary:
    rows_total: int = 0
    people_created: int = 0
    people_matched: int = 0
    tenures_created: int = 0
    tenures_skipped_existing: int = 0

    def __str__(self) -> str:
        return (
            f"{self.rows_total} contract rows | "
            f"people: +{self.people_created} created, {self.people_matched} matched | "
            f"tenures: +{self.tenures_created} created, {self.tenures_skipped_existing} already present"
        )


def _person_key(rec: ContractRecord) -> tuple[str, str]:
    return (rec.last_name.upper(), rec.first_name.upper())


def _full_name(rec: ContractRecord) -> str:
    return f"{rec.first_name} {rec.last_name}".strip()


def _synthetic_email(rec: ContractRecord) -> str:
    slug = f"{rec.first_name}.{rec.last_name}".lower().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c in ".-") or "unknown"
    return f"{slug}@archive.invalid"


def _stored_employer(rec: ContractRecord) -> str:
    return rec.employer or "Unknown"


async def import_personnel(records: Iterable[ContractRecord]) -> ImportSummary:
    """Upsert one inactive User per person and one UserTenure per contract.

    Idempotent: people match by email; tenures already present
    (same start_date + status + employer) are skipped.
    """
    records = list(records)
    summary = ImportSummary(rows_total=len(records))

    people: dict[tuple[str, str], list[ContractRecord]] = {}
    for rec in records:
        people.setdefault(_person_key(rec), []).append(rec)

    async with session_scope() as session:
        for contracts in people.values():
            user = await _resolve_user(session, contracts, summary)
            existing = await _existing_tenure_keys(session, user.id)
            for rec in sorted(contracts, key=lambda r: r.start_date):
                key = (rec.start_date, rec.status, _stored_employer(rec))
                if key in existing:
                    summary.tenures_skipped_existing += 1
                    continue
                session.add(
                    UserTenure(
                        user_id=user.id,
                        status=rec.status,
                        employer=_stored_employer(rec),
                        start_date=rec.start_date,
                        end_date=rec.end_date,
                        notes=rec.notes or None,
                    )
                )
                existing.add(key)
                summary.tenures_created += 1
        await session.commit()

    return summary


async def _resolve_user(session, contracts: list[ContractRecord], summary: ImportSummary) -> User:
    latest = max(contracts, key=lambda r: r.start_date)
    email = next((c.email for c in contracts if c.email), "") or _synthetic_email(latest)

    found = (
        await session.execute(select(User).where(func.lower(User.email) == email.lower()))
    ).scalar_one_or_none()
    if found is not None:
        summary.people_matched += 1
        return found

    user = User(
        email=email,
        hashed_password=NO_LOGIN_PASSWORD,
        is_active=False,
        auth_method=AuthMethod.LOCAL,
        role="",
        full_name=_full_name(latest),
        company=latest.employer or None,
        team=latest.team or None,
        employment_status=latest.status,
    )
    session.add(user)
    await session.flush()  # assign user.id before inserting child tenures
    summary.people_created += 1
    return user


async def _existing_tenure_keys(session, user_id) -> set[tuple]:
    rows = (
        await session.execute(select(UserTenure).where(UserTenure.user_id == user_id))
    ).scalars().all()
    return {(t.start_date, t.status, t.employer) for t in rows}
