from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from threading import Event
from typing import Callable

from automations.booking_models import AutomationCancelled, Progress

COMPLETED_STATUSES = {"ok", "concluida", "completed", "stayed"}
CURRENCY_PATTERN = re.compile(r"R\$\s*[\d.,]+")


def notify(progress: Progress | None, message: str, value: float) -> None:
    if progress:
        progress(message, value)


def checkpoint(cancel: Event | None) -> None:
    if cancel and cancel.is_set():
        raise AutomationCancelled("Execução cancelada pelo usuário.")


def normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(
        char for char in text if not unicodedata.combining(char)
    ).casefold().strip()


def normalized_lines(value: object) -> tuple[str, ...]:
    return tuple(
        normalized
        for line in str(value or "").splitlines()
        if (normalized := normalize(line))
    )


def parse_single_currency(value: str) -> Decimal:
    number = value.replace("R$", "", 1).replace("\xa0", "").replace(" ", "").strip()
    if not number:
        raise ValueError("Valor monetário vazio.")
    if "." in number and "," in number:
        decimal_separator = "." if number.rfind(".") > number.rfind(",") else ","
        thousands_separator = "," if decimal_separator == "." else "."
        number = number.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif "," in number:
        number = (
            number.replace(".", "").replace(",", ".")
            if len(number.rsplit(",", 1)[-1]) == 2
            else number.replace(",", "")
        )
    elif "." in number and len(number.rsplit(".", 1)[-1]) != 2:
        number = number.replace(".", "")
    try:
        return Decimal(number)
    except InvalidOperation as error:
        raise ValueError(f"Valor monetário inválido: {value}") from error


def parse_currency(value: object) -> Decimal:
    text = str(value or "")
    monetary_values = CURRENCY_PATTERN.findall(text)
    if not monetary_values:
        monetary_values = [line.strip() for line in text.splitlines() if line.strip()]
    if not monetary_values:
        raise ValueError("Valor monetário vazio.")
    return sum(map(parse_single_currency, monetary_values), start=Decimal("0"))


def format_currency(value: Decimal) -> str:
    formatted = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {formatted}"


def match_header(
    normalized: dict[str, str],
    aliases: tuple[str, ...],
    prefixes: tuple[str, ...] = (),
) -> str | None:
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return next(
        (
            header
            for key, header in normalized.items()
            if any(key.startswith(prefix) for prefix in prefixes)
        ),
        None,
    )


def source_columns(headers: list[str]) -> dict[str, str | None]:
    normalized = {normalize(header): header for header in headers}
    columns = {
        "reservation": match_header(
            normalized,
            ("numero da reserva", "book number", "booking number", "reservation number"),
        ),
        "guest": match_header(
            normalized,
            ("nome do hospede", "guest name"),
        ),
        "status": match_header(normalized, ("status", "result")),
        "original": match_header(
            normalized,
            (),
            ("valor original", "original amount"),
        ),
        "final": match_header(
            normalized,
            (),
            ("valor final", "final amount"),
        ),
        "commission": match_header(
            normalized,
            (),
            ("valor de comissao", "commission amount"),
        ),
        "notes": match_header(
            normalized,
            (),
            ("observac", "notes", "remarks"),
        ),
    }
    required = ("reservation", "status", "original", "final", "commission")
    missing = [name for name in required if columns[name] is None]
    if missing:
        raise RuntimeError(
            "Colunas obrigatórias não encontradas no relatório da Booking: "
            + ", ".join(missing)
        )
    return columns


def should_compare(record: dict[str, str], columns: dict[str, str | None]) -> bool:
    raw_status = record[str(columns["status"])]
    statuses = normalized_lines(raw_status)
    if len(statuses) > 1 and not grouped_under_same_guest(record, columns):
        return False
    if statuses and all(status in COMPLETED_STATUSES for status in statuses):
        return True
    status = normalize(raw_status)
    notes_key = columns["notes"]
    notes = normalize(record.get(str(notes_key), "")) if notes_key else ""
    cancelled = any(
        token in status
        for token in ("cancel", "nao comparecimento", "no show", "no_show")
    ) or any(
        token in notes for token in ("chargeable cancellation", "no show", "no_show")
    )
    return cancelled and all(
        parse_currency(record[str(columns[key])]) > 0
        for key in ("original", "final", "commission")
    )


def grouped_under_same_guest(
    record: dict[str, str], columns: dict[str, str | None]
) -> bool:
    guest_key = columns.get("guest")
    if not guest_key:
        return False
    guests = normalized_lines(record.get(guest_key, ""))
    return len(guests) > 1 and len(set(guests)) == 1


def calculate_booking_total(
    record: dict[str, str], columns: dict[str, str | None]
) -> Decimal:
    raw_value = record[str(columns["final"])]
    monetary_values = CURRENCY_PATTERN.findall(str(raw_value or ""))
    if len(monetary_values) > 1 and not grouped_under_same_guest(record, columns):
        raise ValueError(
            "Reserva agrupada contém hóspedes diferentes; valores não somados."
        )
    return parse_currency(raw_value)


def consolidate_grouped_record(
    record: dict[str, str], columns: dict[str, str | None]
) -> bool:
    """Collapse one Booking row containing repeated entries for the same guest."""

    guest_key = columns.get("guest")
    guests = normalized_lines(record.get(str(guest_key), "")) if guest_key else ()
    record["Itens agrupados"] = str(max(len(guests), 1))
    if not grouped_under_same_guest(record, columns):
        return False

    monetary_keys = {
        str(columns[key]) for key in ("original", "final", "commission")
    }
    for key, value in tuple(record.items()):
        lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
        if len(lines) <= 1:
            continue
        if key in monetary_keys:
            record[key] = format_currency(parse_currency(value))
            continue
        record[key] = " | ".join(dict.fromkeys(lines))
    return True


def compare_records(
    records: list[dict[str, str]],
    columns: dict[str, str | None],
    total_lookup: Callable[[str], str],
    progress: Progress | None = None,
    cancel: Event | None = None,
    after_record: Callable[[list[dict[str, str]]], None] | None = None,
) -> None:
    eligibility = [should_compare(record, columns) for record in records]
    eligible_count = sum(eligibility)
    processed = 0
    opera_totals: dict[str, str] = {}
    for record, eligible in zip(records, eligibility, strict=True):
        checkpoint(cancel)
        booking_amount: Decimal | None = None
        try:
            booking_amount = calculate_booking_total(record, columns)
            record["Valor Booking calculado"] = format_currency(booking_amount)
        except ValueError:
            record["Valor Booking calculado"] = ""
        record["Valor OPERA"] = ""
        record["Diferença"] = ""
        if not eligible:
            record["Conferência"] = "NÃO CONFERIDA - REGRA"
            continue

        reservation = record[str(columns["reservation"])].strip()
        try:
            if reservation not in opera_totals:
                opera_totals[reservation] = total_lookup(reservation)
            opera_text = opera_totals[reservation]
            if booking_amount is None:
                booking_amount = calculate_booking_total(record, columns)
            difference = booking_amount - parse_currency(opera_text)
            record["Valor OPERA"] = opera_text
            record["Diferença"] = str(difference).replace(".", ",")
            record["Conferência"] = (
                "OK" if abs(difference) <= Decimal("0.01") else "DIVERGENTE"
            )
        except AutomationCancelled:
            raise
        except Exception as error:
            record["Conferência"] = f"ERRO: {error}"

        processed += 1
        if after_record:
            after_record(records)
        notify(
            progress,
            f"Reserva {reservation}: {record['Conferência']}",
            0.30 + 0.65 * processed / max(eligible_count, 1),
        )
