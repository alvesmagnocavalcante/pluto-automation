from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from automations import booking as booking_module
from automations import booking_browser
from automations.booking_domain import consolidate_grouped_record
from automations.booking import (
    BookingConfig,
    BookingDependencies,
    BookingResult,
    compare_records,
    parse_currency,
    run,
    should_compare,
    source_columns,
    wait_for_booking_rows,
)
from automations.booking_models import BOOKING_URL


class BookingTests(TestCase):
    def test_default_booking_url_uses_configured_hotel(self):
        self.assertEqual(
            BOOKING_URL,
            "https://admin.booking.com/hotel/hoteladmin/extranet_ng/"
            "manage/finance_reservations.html",
        )
        self.assertNotIn("hotel_id=", BOOKING_URL)
        self.assertNotIn("ses=", BOOKING_URL)

    def test_browser_is_created_in_visible_mode(self):
        class Options:
            def __init__(self):
                self.arguments = []

            def auto_port(self):
                return self

            def set_pref(self, *_args):
                pass

            def set_argument(self, argument):
                self.arguments.append(argument)

        options = Options()
        browser = object()
        with (
            patch.object(booking_browser, "ChromiumOptions", return_value=options),
            patch.object(booking_browser, "Chromium", return_value=browser) as chromium,
        ):
            result = booking_browser.create_browser()

        self.assertIs(result, browser)
        self.assertIn("--window-size=1920,1080", options.arguments)
        self.assertNotIn("--headless", options.arguments)
        chromium.assert_called_once_with(options)

    def test_booking_field_accepts_semantic_fallback_selector(self):
        class States:
            is_displayed = True

        class Field:
            states = States()

        class Tab:
            url = "https://account.booking.com/sign-in"

            def eles(self, selector):
                if selector == 'css:input[type="password"]':
                    return [Field()]
                return []

        class Browser:
            latest_tab = Tab()

        tab, field = booking_browser.find_booking_field(
            Browser(),
            booking_browser.BOOKING_PASSWORD_SELECTORS,
            "senha",
            cancel=None,
        )

        self.assertIsInstance(tab, Tab)
        self.assertIsInstance(field, Field)

    def test_columns_modal_and_apply_button_accept_english_labels(self):
        class States:
            is_displayed = True

        class HiddenStates:
            is_displayed = False

        class Checkbox:
            states = HiddenStates()

        class Button:
            states = States()
            text = "Apply"

        class Modal:
            states = States()

            def eles(self, selector):
                if selector == 'css:input[type="checkbox"]':
                    return [Checkbox()]
                if selector == "tag:button":
                    return [Button()]
                return []

            def ele(self, _selector, timeout=0):
                return None

        class Tab:
            def __init__(self):
                self.modal = Modal()

            def eles(self, selector):
                if selector == 'css:[role="dialog"]':
                    return [self.modal]
                return []

        tab = Tab()
        modal = booking_browser.find_columns_modal(tab, cancel=None)
        apply_button = booking_browser.find_apply_button(modal)

        self.assertIs(modal, tab.modal)
        self.assertEqual(apply_button.text, "Apply")

    def test_columns_panel_is_found_by_visible_title_and_apply_structure(self):
        class States:
            is_displayed = True

        class Panel:
            states = States()

        class Tab:
            def __init__(self):
                self.panel = Panel()

            def eles(self, selector):
                if selector == booking_browser.COLUMN_PANEL_SELECTOR:
                    return [self.panel]
                return []

        tab = Tab()

        self.assertIs(
            booking_browser.find_columns_modal(tab, cancel=None),
            tab.panel,
        )

    def test_currency_formats(self):
        self.assertEqual(str(parse_currency("R$ 1.234,56")), "1234.56")
        self.assertEqual(str(parse_currency("1,234.56")), "1234.56")
        self.assertEqual(str(parse_currency("1234.56")), "1234.56")

    def test_completed_reservation_is_eligible(self):
        headers = ["Número da reserva", "Nome do hóspede", "Status", "Valor original", "Valor final", "Valor de comissão", "Observações"]
        columns = source_columns(headers)
        row = dict(zip(headers, [
            "123",
            "Ana\nAna\nAna",
            "Concluída\nConcluída\nConcluída",
            "100\n200\n300",
            "100\n200\n300",
            "10\n20\n30",
            "",
        ]))
        self.assertTrue(should_compare(row, columns))

    def test_english_booking_headers_and_status_are_supported(self):
        headers = [
            "Book number",
            "Guest name",
            "Result",
            "Original amount (BRL)",
            "Final amount (BRL)",
            "Commission amount (BRL)",
        ]
        columns = source_columns(headers)
        row = dict(zip(headers, [
            "123",
            "Guest",
            "Stayed",
            "R$ 100,00",
            "R$ 100,00",
            "R$ 16,00",
        ]))

        self.assertEqual(columns["reservation"], "Book number")
        self.assertEqual(columns["status"], "Result")
        self.assertTrue(should_compare(row, columns))

    def test_grouped_reservation_sums_booking_values(self):
        headers = [
            "Número da reserva",
            "Nome do hóspede",
            "Status",
            "Valor original",
            "Valor final",
            "Valor de comissão",
            "Observações",
        ]
        columns = source_columns(headers)
        record = dict(zip(headers, [
            "6269919006",
            "Ana Isabelly Vieira Costa\nAna Isabelly Vieira Costa\nAna Isabelly Vieira Costa",
            "Concluída\nConcluída\nConcluída",
            "R$ 1.657,92\nR$ 1.326,17\nR$ 1.326,17",
            "R$ 1.657,92\nR$ 1.326,17\nR$ 1.326,17",
            "R$ 298,43\nR$ 238,71\nR$ 238,71",
            "",
        ]))

        self.assertTrue(consolidate_grouped_record(record, columns))
        compare_records([record], columns, lambda _reservation: "R$ 4.310,26")

        self.assertEqual(record["Nome do hóspede"], "Ana Isabelly Vieira Costa")
        self.assertEqual(record["Valor final"], "R$ 4.310,26")
        self.assertEqual(record["Itens agrupados"], "3")
        self.assertEqual(record["Valor Booking calculado"], "R$ 4.310,26")
        self.assertEqual(record["Diferença"], "0,00")
        self.assertEqual(record["Conferência"], "OK")

    def test_grouped_reservation_does_not_sum_different_guests(self):
        headers = [
            "Número da reserva",
            "Nome do hóspede",
            "Status",
            "Valor original",
            "Valor final",
            "Valor de comissão",
            "Observações",
        ]
        columns = source_columns(headers)
        record = dict(zip(headers, [
            "123",
            "Hóspede A\nHóspede B",
            "Concluída\nConcluída",
            "R$ 100,00\nR$ 200,00",
            "R$ 100,00\nR$ 200,00",
            "R$ 10,00\nR$ 20,00",
            "",
        ]))

        self.assertFalse(consolidate_grouped_record(record, columns))
        compare_records(
            [record],
            columns,
            lambda _reservation: self.fail("OPERA não deveria ser consultado"),
        )

        self.assertEqual(record["Valor Booking calculado"], "")
        self.assertEqual(record["Conferência"], "NÃO CONFERIDA - REGRA")

    def test_comparison_queries_duplicate_reservation_only_once(self):
        headers = [
            "Book number",
            "Guest name",
            "Result",
            "Original amount (BRL)",
            "Final amount (BRL)",
            "Commission amount (BRL)",
        ]
        columns = source_columns(headers)
        row = ["123", "Ana", "Stayed", "R$ 100,00", "R$ 100,00", "R$ 16,00"]
        records = [dict(zip(headers, row)), dict(zip(headers, row))]
        lookups = []

        compare_records(
            records,
            columns,
            lambda reservation: lookups.append(reservation) or "R$ 100,00",
        )

        self.assertEqual(lookups, ["123"])
        self.assertTrue(
            all(record["Conferência"] == "OK" for record in records)
        )

    def test_config_requires_all_credentials(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                BookingConfig("", "", "", "", Path(directory)).validate()

    def test_config_requires_hotel(self):
        with TemporaryDirectory() as directory:
            config = BookingConfig("user", "pass", "user", "pass", Path(directory))
            with self.assertRaisesRegex(ValueError, "hotel/resort"):
                config.validate()

    def test_result_exposes_each_outcome_count(self):
        result = BookingResult(
            rows=(
                {"Conferência": "OK"},
                {"Conferência": "DIVERGENTE"},
                {"Conferência": "ERRO: indisponível"},
                {"Conferência": "NÃO CONFERIDA - REGRA"},
            ),
            booking_csv=Path("booking.csv"),
            report_csv=Path("report.csv"),
            report_excel=Path("report.xlsx"),
        )

        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.mismatch_count, 1)
        self.assertEqual(result.error_count, 1)
        self.assertEqual(result.not_compared_count, 1)

    def test_table_wait_accepts_fewer_rows_than_page_capacity(self):
        class Row:
            text = "reserva 123"

        class Table:
            def eles(self, selector):
                return [Row()] if selector == "css:tbody > tr" else []

        class Tab:
            def __init__(self):
                self.table = Table()

            def ele(self, _selector, timeout):
                return self.table

        clock = [0, 0, 0, 0, 0.5, 0.5, 2.1, 2.1]
        with (
            patch.object(booking_module, "monotonic", side_effect=clock),
            patch.object(booking_module, "sleep"),
        ):
            table, rows = wait_for_booking_rows(Tab(), page_capacity=100, cancel=None)

        self.assertIsInstance(table, Table)
        self.assertEqual(len(rows), 1)

    def test_table_stability_signature_is_read_in_one_operation(self):
        class Table:
            def __init__(self):
                self.calls = 0

            def run_js(self, _script):
                self.calls += 1
                return ["123\tGuest\tStayed", "456\tGuest 2\tCancelled"]

        class Row:
            @property
            def text(self):
                raise AssertionError("linhas não devem ser lidas individualmente")

        table = Table()
        signature = booking_browser.booking_rows_signature(table, [Row(), Row()])

        self.assertEqual(
            signature,
            ("123\tGuest\tStayed", "456\tGuest 2\tCancelled"),
        )
        self.assertEqual(table.calls, 1)

    def test_select_hotel_uses_profile_search_and_first_result(self):
        class Field:
            def __init__(self):
                self.value = None

            def input(self, value, clear):
                self.value = (value, clear)

        class Wait:
            def doc_loaded(self, timeout):
                self.timeout = timeout

        class Tab:
            wait = Wait()

        field = Field()
        with (
            patch.object(
                booking_browser, "open_hotel_search", return_value=field
            ),
            patch.object(booking_browser, "click_visible_any") as click,
            patch.object(booking_browser, "sleep"),
        ):
            booking_browser.select_hotel(Tab(), "  Resort Teste  ", None)

        self.assertEqual(field.value, ("Resort Teste", True))
        self.assertEqual(
            [call.args[1] for call in click.call_args_list],
            [
                booking_browser.HOTEL_SEARCH_SELECTORS,
                booking_browser.HOTEL_RESULT_SELECTORS,
            ],
        )

    def test_hotel_input_accepts_dynamic_opera_panel_id(self):
        class States:
            is_displayed = True

        class Field:
            states = States()

        class Tab:
            def eles(self, selector):
                if selector == booking_browser.HOTEL_INPUT_SELECTORS[1]:
                    return [Field()]
                return []

        field = booking_browser.find_visible_any(
            Tab(),
            booking_browser.HOTEL_INPUT_SELECTORS,
            "Campo de hotel",
            cancel=None,
        )

        self.assertIsInstance(field, Field)

    def test_hotel_panel_is_reopened_when_first_attempt_does_not_render(self):
        field = object()
        with (
            patch.object(booking_browser, "click_visible") as click,
            patch.object(
                booking_browser,
                "find_visible_any",
                side_effect=[RuntimeError("não abriu"), field],
            ) as find_field,
            patch.object(booking_browser, "sleep"),
        ):
            result = booking_browser.open_hotel_search(object(), cancel=None)

        self.assertIs(result, field)
        self.assertEqual(click.call_count, 4)
        self.assertEqual(find_field.call_count, 2)

    def test_click_visible_can_bypass_missing_element_dimensions(self):
        class States:
            is_displayed = True

        class Scroll:
            def to_see(self):
                raise AssertionError("scroll não deve ser usado no clique JavaScript")

        class Element:
            states = States()
            scroll = Scroll()

            def __init__(self):
                self.clicked_by_js = False

            def click(self, *, by_js=False):
                self.clicked_by_js = by_js

        class Tab:
            def __init__(self, element):
                self.element = element

            def eles(self, _selector):
                return [self.element]

        element = Element()
        with patch.object(booking_browser, "sleep"):
            booking_browser.click_visible(
                Tab(element),
                "xpath://div",
                "Elemento sem dimensão",
                by_js=True,
            )

        self.assertTrue(element.clicked_by_js)

    def test_find_visible_now_disables_drission_implicit_wait(self):
        class States:
            is_displayed = True

        class Element:
            states = States()

        class Tab:
            def __init__(self):
                self.timeout = None

            def eles(self, _selector, *, timeout):
                self.timeout = timeout
                return [Element()]

        tab = Tab()

        result = booking_browser.find_visible_now(tab, "xpath://input")

        self.assertIsNotNone(result)
        self.assertEqual(tab.timeout, 0)

    def test_opera_dynamic_click_uses_semantic_fallback_without_waiting(self):
        class States:
            is_displayed = True

        class Element:
            states = States()

            def __init__(self):
                self.clicked = False

            def click(self, *, by_js=False):
                self.clicked = True

        class Tab:
            def __init__(self, element):
                self.element = element
                self.calls = []

            def eles(self, selector, *, timeout):
                self.calls.append((selector, timeout))
                return [self.element] if selector == "semantic" else []

        element = Element()
        tab = Tab(element)

        booking_browser.click_opera_dynamic_any(
            tab,
            ("dynamic-id", "semantic"),
            "Fechar detalhes",
            cancel=None,
        )

        self.assertTrue(element.clicked)
        self.assertEqual(
            tab.calls,
            [("dynamic-id", 0), ("semantic", 0)],
        )

    def test_opera_reservation_is_committed_without_control_character(self):
        class Field:
            def __init__(self):
                self.inputs = []
                self.scripts = []

            def input(self, value, *, clear=False):
                self.inputs.append((value, clear))

            def run_js(self, script):
                self.scripts.append(script)

        field = Field()

        booking_browser.fill_opera_reservation(field, "6899207394")

        self.assertEqual(field.inputs, [("6899207394", True)])
        self.assertEqual(field.scripts, ["this.blur();"])

    def test_wait_for_text_keeps_polling_until_value_is_loaded(self):
        class Element:
            reads = 0

            @property
            def text(self):
                self.reads += 1
                return "" if self.reads == 1 else "R$ 1.234,56"

        with patch.object(booking_browser, "sleep"):
            value = booking_browser.wait_for_text(
                Element(), "Valor total", cancel=None
            )

        self.assertEqual(value, "R$ 1.234,56")

    def test_opera_total_retries_after_element_is_replaced(self):
        with (
            patch.object(
                booking_browser,
                "_opera_total_once",
                side_effect=[
                    booking_browser.ElementLostError(),
                    "R$ 1.234,56",
                ],
            ) as lookup,
            patch.object(booking_browser, "recover_opera_search") as recover,
        ):
            total = booking_browser.opera_total(
                object(), "5618549915", cancel=None
            )

        self.assertEqual(total, "R$ 1.234,56")
        self.assertEqual(lookup.call_count, 2)
        recover.assert_called_once()

    def test_opera_waits_until_previous_result_is_invalidated(self):
        class PreviousStates:
            def __init__(self):
                self.reads = 0

            @property
            def is_alive(self):
                self.reads += 1
                return self.reads == 1

            is_displayed = True

        class Previous:
            states = PreviousStates()

        tab = object()
        current = object()
        with (
            patch.object(
                booking_browser, "find_visible_now", return_value=current
            ) as find_current,
            patch.object(booking_browser, "sleep"),
        ):
            result = booking_browser.wait_for_new_opera_result(
                tab, Previous(), cancel=None
            )

        self.assertIs(result, current)
        self.assertEqual(find_current.call_count, 2)
        find_current.assert_called_with(tab, booking_browser.RATE_LINK_SELECTOR)

    def test_opera_accepts_reused_element_when_result_row_changes(self):
        class States:
            is_alive = True
            is_displayed = True

        class Previous:
            states = States()

        tab = object()
        current = object()
        with (
            patch.object(
                booking_browser, "find_visible_now", return_value=current
            ),
            patch.object(
                booking_browser,
                "opera_result_signature",
                return_value="589788462 125973062 Couto Adilson R$426.16",
            ),
        ):
            result = booking_browser.wait_for_new_opera_result(
                tab,
                Previous(),
                cancel=None,
                previous_signature="5990328278 Maria Yone Ramos R$545.00",
            )

        self.assertIs(result, current)

    def test_opera_fixed_delays_stay_below_one_and_half_seconds_per_query(self):
        fixed_delay = sum(
            (
                booking_browser.OPERA_INPUT_SETTLE_SECONDS,
                booking_browser.OPERA_DETAIL_SETTLE_SECONDS,
                booking_browser.OPERA_CLOSE_SETTLE_SECONDS,
                booking_browser.OPERA_BETWEEN_QUERIES_SECONDS,
            )
        )

        self.assertLessEqual(fixed_delay, 1.5)

    def test_batch_table_extraction_preserves_headers_and_multiline_values(self):
        class Table:
            def run_js(self, _script):
                return [
                    ["Book number", "Guest name", "Final amount (BRL)"],
                    [["123", "Ana\nAna", "R$ 100,00\nR$ 200,00"]],
                ]

            def eles(self, _selector):
                raise AssertionError("fallback individual não deveria ser usado")

        headers, rows = booking_browser.extract_booking_table(Table(), [])

        self.assertEqual(
            headers,
            ["Book number", "Guest name", "Final amount (BRL)"],
        )
        self.assertEqual(rows, [["123", "Ana\nAna", "R$ 100,00\nR$ 200,00"]])

    def test_column_selection_is_executed_in_one_browser_operation(self):
        class Modal:
            def __init__(self):
                self.scripts = []

            def run_js(self, script):
                self.scripts.append(script)

        modal = Modal()
        booking_browser.select_all_columns(modal, cancel=None)

        self.assertEqual(len(modal.scripts), 1)
        self.assertIn("control.click()", modal.scripts[0])

    def test_columns_panel_uses_exact_xpath_without_drission_search(self):
        class Tab:
            def __init__(self):
                self.scripts = []

            def run_js(self, script):
                self.scripts.append(script)
                return "clicked"

            def ele(self, *_args, **_kwargs):
                raise AssertionError("tab.ele() não deve ser usado na Booking")

            def eles(self, *_args, **_kwargs):
                raise AssertionError("tab.eles() não deve ser usado na Booking")

        tab = Tab()
        with patch.object(booking_browser, "sleep"):
            booking_browser.open_columns_panel(tab, cancel=None)

        self.assertEqual(tab.scripts, [booking_browser.OPEN_COLUMNS_PANEL_SCRIPT])
        self.assertIn(booking_browser.COLUMN_BUTTON_XPATH, tab.scripts[0])

    def test_columns_are_selected_and_applied_without_modal_selector(self):
        class Tab:
            def __init__(self):
                self.scripts = []

            def run_js(self, script):
                self.scripts.append(script)
                if script == booking_browser.COLUMN_SELECTION_SCRIPT:
                    return {"ready": True, "total": 12, "changed": 3}
                if script == booking_browser.APPLY_COLUMNS_SCRIPT:
                    return True
                raise AssertionError("script inesperado")

        tab = Tab()
        with patch.object(booking_browser, "sleep"):
            changed = booking_browser.configure_columns(tab, cancel=None)

        self.assertEqual(changed, 3)
        self.assertEqual(
            tab.scripts,
            [
                booking_browser.COLUMN_SELECTION_SCRIPT,
                booking_browser.APPLY_COLUMNS_SCRIPT,
            ],
        )
        self.assertIn("querySelectorAll('body *')", booking_browser.COLUMN_SELECTION_SCRIPT)

    def test_booking_report_waits_for_rows_before_opening_columns(self):
        class Tab:
            def __init__(self):
                self.reads = 0

            def run_js(self, script):
                self.assert_script = script
                self.reads += 1
                return 0 if self.reads == 1 else 2

            def ele(self, *_args, **_kwargs):
                raise AssertionError("tab.ele() não deve ser usado na Booking")

        with patch.object(booking_browser, "sleep") as sleeper:
            booking_browser.wait_booking_report_ready(Tab(), cancel=None)

        self.assertGreaterEqual(sleeper.call_count, 2)

    def test_booking_report_recovers_when_page_refreshes(self):
        class Tab:
            def __init__(self):
                self.calls = 0

            def run_js(self, _script):
                self.calls += 1
                if self.calls == 1:
                    raise booking_browser.ContextLostError
                return 2

        tab = Tab()
        with patch.object(booking_browser, "sleep"):
            booking_browser.wait_booking_report_ready(tab, cancel=None)

        self.assertEqual(tab.calls, 2)

    def test_booking_table_restarts_column_flow_after_page_refresh(self):
        class Browser:
            latest_tab = object()

        with (
            patch.object(booking_browser, "wait_booking_report_ready") as ready,
            patch.object(
                booking_browser,
                "open_columns_panel",
                side_effect=[booking_browser.BookingPageRefreshed(), None],
            ) as open_panel,
            patch.object(booking_browser, "configure_columns") as configure,
            patch.object(
                booking_browser, "select_all_booking_rows", return_value=100
            ),
            patch.object(
                booking_browser,
                "wait_for_booking_data",
                return_value=(["Book number"], [["123"]]),
            ),
            patch.object(booking_browser, "sleep"),
        ):
            headers, rows = booking_browser.booking_table(Browser(), cancel=None)

        self.assertEqual(ready.call_count, 2)
        self.assertEqual(open_panel.call_count, 2)
        configure.assert_called_once()
        self.assertEqual(headers, ["Book number"])
        self.assertEqual(rows, [["123"]])

    def test_booking_pagination_and_extraction_do_not_use_drission_search(self):
        class Tab:
            def __init__(self):
                self.scripts = []

            def run_js(self, script):
                self.scripts.append(script)
                if script == booking_browser.SELECT_ALL_BOOKING_ROWS_SCRIPT:
                    return "100"
                if script == booking_browser.BOOKING_TABLE_SNAPSHOT_SCRIPT:
                    return [
                        ["Book number", "Guest name", "Final amount (BRL)"],
                        [["123", "Ana", "R$ 100,00"]],
                    ]
                raise AssertionError("script inesperado")

            def ele(self, *_args, **_kwargs):
                raise AssertionError("tab.ele() não deve ser usado na Booking")

        tab = Tab()
        capacity = booking_browser.select_all_booking_rows(tab, cancel=None)
        clock = [0, 0, 0, 0, 0.5, 0.5, 2.1, 2.1]
        headers, rows = booking_browser.wait_for_booking_data(
            tab,
            capacity,
            cancel=None,
            clock=lambda: clock.pop(0),
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(capacity, 100)
        self.assertEqual(headers[0], "Book number")
        self.assertEqual(rows, [["123", "Ana", "R$ 100,00"]])

    def test_run_accepts_injected_browser_dependencies_and_counts_errors(self):
        headers = [
            "Número da reserva", "Status", "Valor original", "Valor final",
            "Valor de comissão", "Observações",
        ]
        rows = [["123", "Concluída", "100", "100", "10", ""]]
        opera_steps = []

        class Browser:
            def quit(self):
                pass

        def unavailable_total(*_args):
            raise RuntimeError("indisponível")

        dependencies = BookingDependencies(
            browser_factory=Browser,
            booking_login=lambda *_args: None,
            table_reader=lambda *_args: (headers, rows),
            opera_login=lambda *_args: opera_steps.append("login") or object(),
            reservations_opener=lambda *_args: opera_steps.append("reservations"),
            total_reader=lambda *_args: (
                opera_steps.append("total") or unavailable_total()
            ),
            hotel_selector=lambda _tab, hotel, _cancel: opera_steps.append(
                f"hotel:{hotel}"
            ),
        )

        with TemporaryDirectory() as directory:
            config = BookingConfig(
                "user", "pass", "user", "pass", Path(directory), hotel_name="Resort Teste"
            )
            result = run(config, dependencies=dependencies)

            self.assertEqual(result.matched_count, 0)
            self.assertEqual(result.divergent_count, 1)
            self.assertTrue(result.report_excel.exists())
            self.assertEqual(
                opera_steps,
                ["login", "hotel:Resort Teste", "reservations", "total"],
            )

    def test_run_closes_browser_when_booking_extraction_fails(self):
        browsers = []

        class Browser:
            def __init__(self):
                self.closed = False
                browsers.append(self)

            def quit(self):
                self.closed = True

        def fail_extraction(*_args):
            raise RuntimeError("falha na extração")

        dependencies = BookingDependencies(
            browser_factory=Browser,
            booking_login=lambda *_args: None,
            table_reader=fail_extraction,
            opera_login=lambda *_args: object(),
            reservations_opener=lambda *_args: None,
            total_reader=lambda *_args: "0",
        )

        with TemporaryDirectory() as directory:
            config = BookingConfig(
                "user", "pass", "user", "pass", Path(directory), hotel_name="Resort Teste"
            )
            with self.assertRaisesRegex(RuntimeError, "falha na extração"):
                run(config, dependencies=dependencies)

        self.assertEqual(len(browsers), 1)
        self.assertTrue(browsers[0].closed)
