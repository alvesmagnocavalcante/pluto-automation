from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Callable

BOOKING_URL = (
    "https://admin.booking.com/hotel/hoteladmin/extranet_ng/"
    "manage/finance_reservations.html"
)
OPERA_URL = (
    "https://mtcu7.oraclehospitality.us-ashburn-1.ocs.oraclecloud.com/"
    "CARMEL/operacloud/faces/opera-cloud-index/OperaCloud"
)

Progress = Callable[[str, float], None]


class AutomationCancelled(RuntimeError):
    """Raised when the user requests cancellation of the automation."""


@dataclass(frozen=True)
class BookingConfig:
    booking_username: str
    booking_password: str
    opera_username: str
    opera_password: str
    output_dir: Path
    booking_url: str = BOOKING_URL
    opera_url: str = OPERA_URL
    hotel_name: str = ""

    def validate(self) -> None:
        credentials = (
            ("usuário Booking", self.booking_username),
            ("senha Booking", self.booking_password),
            ("usuário OPERA", self.opera_username),
            ("senha OPERA", self.opera_password),
            ("hotel/resort", self.hotel_name.strip()),
        )
        missing = [name for name, value in credentials if not value]
        if missing:
            raise ValueError("Preencha: " + ", ".join(missing))


@dataclass(frozen=True)
class BookingResult:
    rows: tuple[dict[str, str], ...]
    booking_csv: Path
    report_csv: Path
    report_excel: Path

    @property
    def matched_count(self) -> int:
        return sum(row.get("Conferência") == "OK" for row in self.rows)

    @property
    def divergent_count(self) -> int:
        return sum(
            row.get("Conferência") == "DIVERGENTE"
            or row.get("Conferência", "").startswith("ERRO:")
            for row in self.rows
        )

    @property
    def mismatch_count(self) -> int:
        return sum(row.get("Conferência") == "DIVERGENTE" for row in self.rows)

    @property
    def error_count(self) -> int:
        return sum(
            row.get("Conferência", "").startswith("ERRO:") for row in self.rows
        )

    @property
    def not_compared_count(self) -> int:
        return sum(
            row.get("Conferência") == "NÃO CONFERIDA - REGRA"
            for row in self.rows
        )


@dataclass(frozen=True)
class BookingDependencies:
    """External browser operations injected into the orchestration service."""

    browser_factory: Callable[[], Any]
    booking_login: Callable[[Any, BookingConfig, Event | None], None]
    table_reader: Callable[[Any, Event | None], tuple[list[str], list[list[str]]]]
    opera_login: Callable[[Any, BookingConfig, Event | None], Any]
    reservations_opener: Callable[[Any, Event | None], None]
    total_reader: Callable[[Any, str, Event | None], str]
    hotel_selector: Callable[[Any, str, Event | None], None] | None = None
