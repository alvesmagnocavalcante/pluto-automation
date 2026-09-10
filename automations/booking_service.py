from __future__ import annotations

from threading import Event
from time import monotonic

from automations.booking_domain import (
    compare_records,
    consolidate_grouped_record,
    notify,
    source_columns,
)
from automations.booking_models import BookingConfig, BookingDependencies, BookingResult, Progress
from automations.booking_reports import save_report_csv, save_report_excel, write_booking_csv


class BookingReconciliationService:
    """Orchestrates the workflow using injected browser operations."""

    def __init__(self, dependencies: BookingDependencies) -> None:
        self._dependencies = dependencies

    def run(self, config: BookingConfig, progress: Progress | None = None, cancel: Event | None = None) -> BookingResult:
        config.validate()
        config.output_dir.mkdir(parents=True, exist_ok=True)
        booking_csv = config.output_dir / "reservas_booking.csv"
        report_csv = config.output_dir / "conferencia_booking_opera.csv"
        report_excel = config.output_dir / "conferencia_booking_opera.xlsx"
        booking_browser = opera_browser = None
        try:
            notify(progress, "Abrindo a Booking", 0.03)
            booking_browser = self._dependencies.browser_factory()
            self._dependencies.booking_login(booking_browser, config, cancel)
            notify(progress, "Extraindo reservas da Booking", 0.12)
            headers, values = self._dependencies.table_reader(booking_browser, cancel)
            write_booking_csv(booking_csv, headers, values)
            booking_browser.quit()
            booking_browser = None

            records = [dict(zip(headers, row)) for row in values]
            columns = source_columns(headers)
            for record in records:
                consolidate_grouped_record(record, columns)
            report_headers = [
                *headers,
                "Itens agrupados",
                "Valor Booking calculado",
                "Valor OPERA",
                "Diferença",
                "Conferência",
            ]
            notify(progress, f"{len(records)} reservas extraídas; abrindo o OPERA", 0.25)
            opera_browser = self._dependencies.browser_factory()
            tab = self._dependencies.opera_login(opera_browser, config, cancel)
            notify(progress, f"Selecionando hotel/resort: {config.hotel_name}", 0.28)
            if self._dependencies.hotel_selector is None:
                raise RuntimeError("Seletor de hotel/resort não configurado.")
            self._dependencies.hotel_selector(tab, config.hotel_name, cancel)
            self._dependencies.reservations_opener(tab, cancel)
            last_checkpoint = monotonic()

            def save_checkpoint(current: list[dict[str, str]]) -> None:
                nonlocal last_checkpoint
                now = monotonic()
                if now - last_checkpoint < 5:
                    return
                save_report_csv(report_csv, report_headers, current)
                last_checkpoint = now

            compare_records(
                records,
                columns,
                total_lookup=lambda reservation: self._dependencies.total_reader(tab, reservation, cancel),
                progress=progress,
                cancel=cancel,
                after_record=save_checkpoint,
            )
            save_report_csv(report_csv, report_headers, records)
            save_report_excel(report_excel, report_headers, records)
            notify(progress, "Conferência concluída", 1.0)
            return BookingResult(tuple(records), booking_csv, report_csv, report_excel)
        finally:
            self._close_browsers(booking_browser, opera_browser)

    @staticmethod
    def _close_browsers(*browsers: object | None) -> None:
        for browser in browsers:
            if browser is None:
                continue
            try:
                browser.quit()  # type: ignore[attr-defined]
            except Exception:
                pass
