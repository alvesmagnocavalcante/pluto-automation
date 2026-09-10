"""Public facade and composition root for Booking × OPERA reconciliation."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from time import monotonic, sleep

from automations import booking_browser
from automations.booking_domain import (
    checkpoint,
    compare_records,
    normalize,
    parse_currency,
    should_compare,
    source_columns,
)
from automations.booking_models import (
    BOOKING_URL,
    OPERA_URL,
    AutomationCancelled,
    BookingConfig,
    BookingDependencies,
    BookingResult,
    Progress,
)
from automations.booking_reports import save_report_csv, save_report_excel, write_booking_csv
from automations.booking_service import BookingReconciliationService

__all__ = [
    "AutomationCancelled",
    "BookingConfig",
    "BookingDependencies",
    "BookingResult",
    "booking_row_values",
    "booking_table",
    "checkpoint",
    "click_visible",
    "compare_records",
    "config_from_env",
    "create_browser",
    "default_dependencies",
    "find_visible",
    "login_booking",
    "login_opera",
    "normalize",
    "open_reservations",
    "opera_total",
    "parse_currency",
    "run",
    "select_hotel",
    "save_report_csv",
    "save_report_excel",
    "should_compare",
    "source_columns",
    "wait_for_booking_rows",
    "write_booking_csv",
]

create_browser = booking_browser.create_browser
find_visible = booking_browser.find_visible
click_visible = booking_browser.click_visible
login_booking = booking_browser.login_booking
booking_table = booking_browser.booking_table
booking_row_values = booking_browser.booking_row_values
login_opera = booking_browser.login_opera
open_reservations = booking_browser.open_reservations
opera_total = booking_browser.opera_total
select_hotel = booking_browser.select_hotel


def wait_for_booking_rows(tab, page_capacity: int | None, cancel: Event | None):
    """Compatibility wrapper that keeps time dependencies patchable in tests."""

    return booking_browser.wait_for_booking_rows(
        tab, page_capacity, cancel, clock=monotonic, sleeper=sleep
    )


def default_dependencies() -> BookingDependencies:
    return BookingDependencies(
        browser_factory=create_browser,
        booking_login=login_booking,
        table_reader=booking_table,
        opera_login=login_opera,
        reservations_opener=open_reservations,
        total_reader=opera_total,
        hotel_selector=select_hotel,
    )


def run(
    config: BookingConfig,
    progress: Progress | None = None,
    cancel: Event | None = None,
    dependencies: BookingDependencies | None = None,
) -> BookingResult:
    service = BookingReconciliationService(dependencies or default_dependencies())
    return service.run(config, progress, cancel)


def config_from_env(output_dir: Path | None = None) -> BookingConfig:
    return BookingConfig(
        os.getenv("BOOKING_USERNAME", ""),
        os.getenv("BOOKING_PASSWORD", ""),
        os.getenv("OPERA_USERNAME", ""),
        os.getenv("OPERA_PASSWORD", ""),
        output_dir=output_dir or Path(os.getenv("PLUTO_OUTPUT_DIR", "output")),
        hotel_name=os.getenv("OPERA_HOTEL", ""),
        booking_url=os.getenv("BOOKING_URL", BOOKING_URL),
        opera_url=os.getenv("OPERA_URL", OPERA_URL),
    )
