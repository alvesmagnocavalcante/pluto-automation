from __future__ import annotations

from threading import Event
from time import monotonic, sleep
from typing import Any, Callable
from urllib.parse import urlsplit

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import ContextLostError, ElementLostError, NoRectError

from automations.booking_domain import checkpoint, normalize
from automations.booking_models import AutomationCancelled, BookingConfig

POLL_INTERVAL = 0.25
ACTION_SETTLE_SECONDS = 0.25
PAGE_SETTLE_SECONDS = 0.75
RESULT_SETTLE_SECONDS = 0.75
TABLE_TIMEOUT = 60
TABLE_STABLE_SECONDS = 2.0
BOOKING_LOGIN_TIMEOUT = 60
BOOKING_REPORT_TIMEOUT = 60
BOOKING_REPORT_SETTLE_SECONDS = 1.5
BOOKING_REFRESH_RETRIES = 3
OPERA_ACTION_SETTLE_SECONDS = 0.25
OPERA_INPUT_SETTLE_SECONDS = 1.0
OPERA_DETAIL_SETTLE_SECONDS = 0.1
OPERA_CLOSE_SETTLE_SECONDS = 0.1
OPERA_BETWEEN_QUERIES_SECONDS = 0.1
OPERA_RESULT_TIMEOUT = 10
OPERA_QUERY_RETRIES = 3

TRANSIENT_BROWSER_ERRORS = (ContextLostError, ElementLostError, NoRectError)
OPERA_RETRYABLE_ERRORS = (*TRANSIENT_BROWSER_ERRORS, RuntimeError)


class BookingPageRefreshed(RuntimeError):
    """Signals that Booking invalidated the JavaScript execution context."""

BOOKING_USERNAME_SELECTORS = (
    "#loginname",
    'css:input[name="loginname"]',
    'css:input[name="username"]',
    'css:input[type="email"]',
    'css:input[autocomplete="username"]',
)
BOOKING_PASSWORD_SELECTORS = (
    "#password",
    'css:input[name="password"]',
    'css:input[type="password"]',
    'css:input[autocomplete="current-password"]',
)
COLUMN_MODAL_SELECTORS = (
    'css:[role="dialog"]',
    'css:[role="menu"]',
    'css:[data-test-id*="column"]',
    'xpath://*[normalize-space()="Selecione as colunas" or normalize-space()="Select columns"]/ancestor::*[.//input[@type="checkbox"]][1]',
    'xpath://*[normalize-space()="Selecione as colunas" or normalize-space()="Select columns"]/ancestor::*[.//*[@role="checkbox"]][1]',
    'xpath://button[normalize-space()="Aplicar" or normalize-space()="Apply"]/ancestor::*[.//input[@type="checkbox"]][1]',
    'xpath://button[normalize-space()="Aplicar" or normalize-space()="Apply"]/ancestor::*[.//*[@role="checkbox"]][1]',
)
COLUMN_CHECKBOX_SELECTORS = (
    'css:input[type="checkbox"]',
    'css:[role="checkbox"]',
)
COLUMN_BUTTON_XPATH = (
    "/html/body/div[1]/div/div[2]/div/div/div/main/div/div/"
    "div[2]/div[2]/div[2]/span/button"
)
OPEN_COLUMNS_PANEL_SCRIPT = f"""
const button = document.evaluate(
    {COLUMN_BUTTON_XPATH!r}, document, null,
    XPathResult.FIRST_ORDERED_NODE_TYPE, null
).singleNodeValue;
if (!button) return 'not-found';
const style = window.getComputedStyle(button);
const rect = button.getBoundingClientRect();
if (style.display === 'none' || style.visibility === 'hidden' ||
        rect.width <= 0 || rect.height <= 0) return 'not-visible';
if (button.disabled || button.getAttribute('aria-disabled') === 'true')
    return 'disabled';
button.scrollIntoView({{block: 'center', inline: 'center'}});
button.click();
return 'clicked';
"""
BOOKING_REPORT_ROW_COUNT_SCRIPT = """
return Array.from(document.querySelectorAll('table'))
    .filter(table => {
        const rect = table.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    })
    .reduce((count, table) => Math.max(
        count, table.querySelectorAll('tbody > tr').length
    ), 0);
"""
SELECT_ALL_BOOKING_ROWS_SCRIPT = """
const select = document.querySelector(
    'select[aria-label="itemsPerPageDisplayed"]'
);
if (!select || !select.options.length) return null;
const value = select.options[select.options.length - 1].value;
const setter = Object.getOwnPropertyDescriptor(
    HTMLSelectElement.prototype, 'value'
).set;
setter.call(select, value);
select.dispatchEvent(new Event('input', {bubbles: true}));
select.dispatchEvent(new Event('change', {bubbles: true}));
return value;
"""
BOOKING_TABLE_SNAPSHOT_SCRIPT = r"""
const clean = value => (value || '').replace(/\u00a0/g, ' ').trim();
const tables = Array.from(document.querySelectorAll('table')).filter(table => {
    const rect = table.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 &&
        table.querySelectorAll('tbody > tr').length > 0;
});
const table = tables.find(candidate => {
    const headers = Array.from(candidate.querySelectorAll('thead th'))
        .map(cell => clean(cell.innerText).toLowerCase());
    return headers.some(header =>
        header.includes('book number') ||
        header.includes('booking number') ||
        header.includes('número da reserva') ||
        header.includes('numero da reserva')
    );
}) || tables[0];
if (!table) return [[], []];
const headers = Array.from(table.querySelectorAll('thead th'))
    .map(cell => clean(cell.innerText));
const rows = Array.from(table.querySelectorAll('tbody > tr')).map(row =>
    Array.from(row.querySelectorAll('td')).map(cell => {
        const checkbox = cell.querySelector('input[type="checkbox"]');
        return checkbox ? (checkbox.checked ? 'Sim' : 'Não')
            : clean(cell.innerText);
    })
);
return [headers, rows];
"""
COLUMN_SELECTION_SCRIPT = r"""
const normalizeText = value => (value || '').replace(/\s+/g, ' ')
    .trim().toLowerCase();
const isVisible = element => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
        rect.width > 0 && rect.height > 0;
};
const hasApplyLabel = element => [
    element.innerText,
    element.textContent,
    element.value,
    element.getAttribute('aria-label'),
    element.getAttribute('title')
].some(value => {
    const label = normalizeText(value);
    return label === 'apply' || label === 'aplicar' ||
        label.startsWith('apply ') || label.startsWith('aplicar ');
});
const applyLabel = Array.from(document.querySelectorAll('body *'))
    .reverse()
    .find(element => isVisible(element) && hasApplyLabel(element));
const apply = applyLabel && (
    applyLabel.closest(
        'button, [role="button"], input[type="button"], input[type="submit"]'
    ) || applyLabel
);
if (!apply) return {ready: false, reason: 'apply-not-found'};

let panel = apply.parentElement;
let controls = [];
while (panel && panel !== document.body) {
    const inputs = Array.from(
        panel.querySelectorAll('input[type="checkbox"]')
    );
    const roles = Array.from(panel.querySelectorAll('[role="checkbox"]'));
    controls = inputs.length ? inputs : roles;
    if (controls.length) break;
    panel = panel.parentElement;
}
if (!controls.length) return {ready: false, reason: 'checkboxes-not-found'};

let changed = 0;
for (const control of controls) {
    const disabled = Boolean(control.disabled) ||
        control.getAttribute('aria-disabled') === 'true';
    const checked = Boolean(control.checked) ||
        control.getAttribute('aria-checked') === 'true';
    if (!disabled && !checked) {
        control.click();
        changed += 1;
    }
}
return {ready: true, total: controls.length, changed};
"""
APPLY_COLUMNS_SCRIPT = r"""
const normalizeText = value => (value || '').replace(/\s+/g, ' ')
    .trim().toLowerCase();
const hasApplyLabel = element => [
    element.innerText,
    element.textContent,
    element.value,
    element.getAttribute('aria-label'),
    element.getAttribute('title')
].some(value => {
    const label = normalizeText(value);
    return label === 'apply' || label === 'aplicar' ||
        label.startsWith('apply ') || label.startsWith('aplicar ');
});
const isVisible = element => {
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' &&
        rect.width > 0 && rect.height > 0;
};
const applyLabel = Array.from(document.querySelectorAll('body *'))
    .reverse()
    .find(element => isVisible(element) && hasApplyLabel(element));
const apply = applyLabel && (
    applyLabel.closest(
        'button, [role="button"], input[type="button"], input[type="submit"]'
    ) || applyLabel
);
if (!apply) return false;
apply.click();
return true;
"""
APPLY_BUTTON_LABELS = ("aplicar", "apply", "concluir", "done", "salvar", "save")
COLUMN_PANEL_SELECTOR = (
    'xpath://*[normalize-space()="Select columns" or '
    'normalize-space()="Selecione as colunas"]/'
    'ancestor::*[.//button[normalize-space()="Apply" or '
    'normalize-space()="Aplicar"]][1]'
)

USERNAME_SELECTOR = 'xpath://*[@id="idcs-signin-basic-signin-form-username"]'
PASSWORD_SELECTOR = 'xpath://*[@id="idcs-signin-basic-signin-form-password|input"]'
LOGIN_BUTTON_SELECTOR = 'xpath://*[@id="idcs-signin-basic-signin-form-submit"]/button'
CONTINUE_BUTTON_SELECTOR = 'xpath://*[@id="ode_init_ovrdbtn"]'
PROFILE_SELECTOR = 'xpath://*[@id="pt1:oc_pg_pt:ode_pg_mnhdr_rght_cntnt_lnk"]'
CHANGE_LOCATION_SELECTOR = (
    "xpath:/html/body/div[1]/form/div/div[2]/div/table/tbody/tr[2]/td[2]/table/"
    "tbody/tr/td/div/div/div[2]/div/div[1]/div[1]/a/span"
)
HOTEL_INPUT_SELECTOR = 'xpath://*[@id="pt1:oc_pg_pt:pt_r1:0:pt1:oc_pnl_lstng_tmpl:oc_pnl_tmpl_323z8b:oc_pnl_lstng_vw_srch_swtchr:odec_srch_swtchr_advncd_sf:fe2:it1:odec_it_it::content"]'
HOTEL_SEARCH_SELECTOR = 'xpath://*[@id="pt1:oc_pg_pt:pt_r1:0:pt1:oc_pnl_lstng_tmpl:oc_pnl_tmpl_323z8b:oc_pnl_lstng_vw_srch_swtchr:odec_srch_swtchr_advncd_sf:odec_srch_swtchr_advncd_srch_btn"]'
HOTEL_RESULT_SELECTOR = 'xpath://*[@id="pt1:oc_pg_pt:pt_r1:0:ab1:odec_axn_br_axns_pstv_i:0:odec_axn_br_axn_pstv"]'
HOTEL_INPUT_SELECTORS = (
    HOTEL_INPUT_SELECTOR,
    'xpath://input[contains(@id, "oc_pnl_lstng_vw_srch_swtchr") and contains(@id, "odec_it_it::content")]',
    'xpath://input[contains(@id, "odec_srch_swtchr_advncd_sf") and @type="text"]',
)
HOTEL_SEARCH_SELECTORS = (
    HOTEL_SEARCH_SELECTOR,
    'xpath://*[contains(@id, "odec_srch_swtchr_advncd_srch_btn")]',
)
HOTEL_RESULT_SELECTORS = (
    HOTEL_RESULT_SELECTOR,
    'xpath://*[contains(@id, "odec_axn_br_axns_pstv_i:0") and contains(@id, "odec_axn_br_axn_pstv")]',
)
BOOKINGS_SELECTOR = 'xpath://*[@id="pt1:oc_pg_pt:dm1:odec_drpmn_mb_grp:1:odec_drpmn_mb_mn"]/div'
RESERVATIONS_SELECTOR = 'xpath://*[@id="pt1:oc_pg_pt:dm1:odec_drpmn_mb_grp:1:odec_drpmn_mb_mn_grp:2:odec_drpmn_mb_mn_si"]/td[2]'
MANAGE_RESERVATION_SELECTOR = 'xpath://*[@id="pt1:oc_pg_pt:dm1:odec_drpmn_mb_grp:1:odec_drpmn_mb_mn_grp:2:odec_drpmn_mb_mn_si_grp:2:odec_drpmn_mb_mn_grp_itm"]'
SEARCH_MODE_SELECTOR = 'xpath://*[@id="pt1:oc_pg_pt:mainRegion:1:pt1:oc_srch_tmpl_167b9q:ode_bscrn_tmpl:oc_srch_swtchr:odec_srch_swtchr_advncd_sf:odec_srch_swtchr_advncd_swtch_lnk"]'
RESERVATION_INPUT_SELECTOR = 'xpath://*[@id="pt1:oc_pg_pt:mainRegion:1:pt1:oc_srch_tmpl_167b9q:ode_bscrn_tmpl:oc_srch_swtchr:odec_srch_swtchr_bsc_ts:odec_ts_sbfrm:odec_ts_inpt::content"]'
SEARCH_BUTTON_SELECTOR = 'xpath://*[@id="pt1:oc_pg_pt:mainRegion:1:pt1:oc_srch_tmpl_167b9q:ode_bscrn_tmpl:oc_srch_swtchr:odec_srch_swtchr_bsc_ts:odec_ts_sbfrm:odec_ts_srch"]'
RATE_LINK_SELECTOR = 'xpath://*[contains(@id, "oc_srch_rslts_tbl_tmpl") and contains(@id, ":ca3:occ_crncy_amt_lnk::text")]'
TOTAL_VALUE_SELECTOR = 'xpath://*[contains(@id, "oc_srch_rslts_tbl_tmpl") and contains(@id, ":CurrencyAmount245:occ_crncy_amt")]'
CLOSE_RATE_SELECTOR = 'xpath://*[contains(@id, "oc_srch_rslts_tbl_tmpl") and contains(@id, ":oc_pnl_axnbr:odec_axn_br_axns_pstv")]'
CLOSE_RATE_SELECTORS = (
    CLOSE_RATE_SELECTOR,
    'xpath://*[normalize-space()="Close" or normalize-space()="Fechar"]/'
    'ancestor-or-self::*[self::button or self::a or @role="button"][1]',
)
OPERA_RESULT_SIGNATURE_SCRIPT = r"""
const rates = Array.from(document.querySelectorAll(
    '[id*="oc_srch_rslts_tbl_tmpl"][id*="occ_crncy_amt_lnk"]'
));
const rate = rates.find(element => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 &&
        style.display !== 'none' && style.visibility !== 'hidden';
}) || rates[0];
let row = rate && rate.closest('tr');
let resultRow = row;
while (row) {
    const text = (row.innerText || '').replace(/\s+/g, ' ').trim();
    if (text.length >= 40) {
        resultRow = row;
        break;
    }
    const parentRow = row.parentElement && row.parentElement.closest('tr');
    if (!parentRow) break;
    resultRow = parentRow;
    row = parentRow;
}
return (resultRow && resultRow.innerText || '').replace(/\u00a0/g, ' ')
    .replace(/\s+/g, ' ').trim();
"""


def create_browser() -> Chromium:
    options = ChromiumOptions().auto_port()
    options.set_pref("credentials_enable_service", False)
    options.set_pref("profile.password_manager_enabled", False)
    options.set_argument("--disable-save-password-bubble")
    options.set_argument("--disable-features=PasswordManagerOnboarding,PasswordLeakDetection")
    options.set_argument("--window-size=1920,1080")
    return Chromium(options)


def find_visible(tab: Any, selector: str, description: str, cancel: Event | None = None, timeout: int = 30) -> Any:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        checkpoint(cancel)
        try:
            elements = tab.eles(selector)
        except TRANSIENT_BROWSER_ERRORS:
            sleep(POLL_INTERVAL)
            continue
        for element in elements:
            try:
                if element.states.is_displayed:
                    return element
            except TRANSIENT_BROWSER_ERRORS:
                continue
        sleep(POLL_INTERVAL)
    raise RuntimeError(f"{description} não encontrado.")


def click_visible(
    tab: Any,
    selector: str,
    description: str,
    cancel: Event | None = None,
    *,
    by_js: bool = False,
    settle_seconds: float = ACTION_SETTLE_SECONDS,
    timeout: int = 30,
) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        checkpoint(cancel)
        try:
            element = find_visible(tab, selector, description, cancel, timeout=5)
            if by_js:
                element.click(by_js=True)
            else:
                element.scroll.to_see()
                sleep(ACTION_SETTLE_SECONDS)
                element.click()
            if settle_seconds:
                sleep(settle_seconds)
            return
        except TRANSIENT_BROWSER_ERRORS:
            sleep(POLL_INTERVAL)
    raise RuntimeError(f"{description} permaneceu sem dimensão.")


def find_visible_any(
    tab: Any,
    selectors: tuple[str, ...],
    description: str,
    cancel: Event | None = None,
    timeout: int = 30,
) -> Any:
    """Find the first visible element among stable and dynamic selectors."""

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        checkpoint(cancel)
        for selector in selectors:
            try:
                elements = tab.eles(selector)
            except TRANSIENT_BROWSER_ERRORS:
                continue
            for element in elements:
                try:
                    if element.states.is_displayed:
                        return element
                except TRANSIENT_BROWSER_ERRORS:
                    continue
        sleep(POLL_INTERVAL)
    raise RuntimeError(f"{description} não encontrado.")


def click_visible_any(
    tab: Any,
    selectors: tuple[str, ...],
    description: str,
    cancel: Event | None = None,
    *,
    settle_seconds: float = OPERA_ACTION_SETTLE_SECONDS,
    timeout: int = 30,
) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        checkpoint(cancel)
        try:
            element = find_visible_any(
                tab, selectors, description, cancel, timeout=5
            )
            element.scroll.to_see()
            sleep(OPERA_ACTION_SETTLE_SECONDS)
            element.click()
            if settle_seconds:
                sleep(settle_seconds)
            return
        except (RuntimeError, *TRANSIENT_BROWSER_ERRORS):
            sleep(POLL_INTERVAL)
    raise RuntimeError(f"{description} não respondeu ao clique.")


def find_booking_field(
    browser: Chromium,
    selectors: tuple[str, ...],
    description: str,
    cancel: Event | None,
    timeout: int = BOOKING_LOGIN_TIMEOUT,
) -> tuple[Any, Any]:
    deadline = monotonic() + timeout
    tab = browser.latest_tab
    while monotonic() < deadline:
        checkpoint(cancel)
        tab = browser.latest_tab
        for selector in selectors:
            try:
                elements = tab.eles(selector)
                field = next(
                    (element for element in elements if element.states.is_displayed),
                    None,
                )
            except Exception:
                field = None
            if field:
                return tab, field
        sleep(POLL_INTERVAL)
    current_url = str(getattr(tab, "url", "indisponível"))
    parsed_url = urlsplit(current_url)
    safe_location = (
        f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
        if parsed_url.scheme and parsed_url.netloc
        else "indisponível"
    )
    raise RuntimeError(
        f"Campo de {description} da Booking não encontrado após {timeout}s. "
        f"Página atual: {safe_location}"
    )


def login_booking(browser: Chromium, config: BookingConfig, cancel: Event | None) -> None:
    tab = browser.latest_tab
    tab.get(config.booking_url)
    tab.wait.doc_loaded(timeout=60)
    sleep(PAGE_SETTLE_SECONDS)
    fields = (
        (BOOKING_USERNAME_SELECTORS, config.booking_username, "usuário"),
        (BOOKING_PASSWORD_SELECTORS, config.booking_password, "senha"),
    )
    for selectors, value, description in fields:
        checkpoint(cancel)
        tab, field = find_booking_field(
            browser, selectors, description, cancel
        )
        field.input(value, clear=True)
        sleep(ACTION_SETTLE_SECONDS)
        submit_button = field.ele("xpath:./ancestor::form//button[@type='submit']")
        if not submit_button:
            raise RuntimeError(
                f"Botão de envio do campo de {description} da Booking não encontrado."
            )
        try:
            submit_button.click()
        except NoRectError:
            submit_button.click(by_js=True)
        sleep(PAGE_SETTLE_SECONDS)
        tab.wait.doc_loaded(timeout=60)


def find_columns_modal(
    tab: Any, cancel: Event | None, timeout: int = 30
) -> Any:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        checkpoint(cancel)
        for candidate in tab.eles(COLUMN_PANEL_SELECTOR):
            try:
                if candidate.states.is_displayed:
                    return candidate
            except Exception:
                continue
        for selector in COLUMN_MODAL_SELECTORS:
            for candidate in tab.eles(selector):
                try:
                    visible = candidate.states.is_displayed
                    has_checkboxes = bool(find_column_checkboxes(candidate))
                except Exception:
                    continue
                if visible and has_checkboxes:
                    return candidate
        try:
            apply_button = find_apply_button(tab)
        except RuntimeError:
            apply_button = None
        if apply_button:
            for level in range(1, 9):
                try:
                    candidate = apply_button.parent(level)
                    if find_column_checkboxes(candidate):
                        return candidate
                except Exception:
                    continue
        sleep(POLL_INTERVAL)
    raise RuntimeError("Janela de seleção das colunas não encontrada.")


def find_apply_button(modal: Any) -> Any:
    for button in modal.eles("tag:button"):
        try:
            if (
                button.states.is_displayed
                and normalize(button.text).startswith(APPLY_BUTTON_LABELS)
            ):
                return button
        except Exception:
            continue
    submit = modal.ele('css:button[type="submit"]', timeout=0)
    if submit and submit.states.is_displayed:
        return submit
    raise RuntimeError('Botão "Aplicar/Apply" não encontrado.')


def find_column_checkboxes(container: Any) -> list[Any]:
    checkboxes: list[Any] = []
    seen: set[int] = set()
    for selector in COLUMN_CHECKBOX_SELECTORS:
        for checkbox in container.eles(selector):
            identity = id(checkbox)
            if identity in seen:
                continue
            # A Booking estiliza o checkbox e mantém o input nativo oculto.
            # O input ainda é válido e pode ser acionado com click(by_js=True).
            checkboxes.append(checkbox)
            seen.add(identity)
    return checkboxes


def select_column_checkbox(checkbox: Any) -> bool:
    try:
        enabled = checkbox.states.is_enabled
    except Exception:
        enabled = normalize(checkbox.attr("aria-disabled")) != "true"
    try:
        checked = checkbox.states.is_checked
    except Exception:
        checked = normalize(checkbox.attr("aria-checked")) == "true"
    if enabled and not checked:
        checkbox.click(by_js=True)
        return True
    return False


def select_all_columns(modal: Any, cancel: Event | None) -> None:
    checkpoint(cancel)
    try:
        modal.run_js(
            """
            const controls = this.querySelectorAll(
                'input[type="checkbox"], [role="checkbox"]'
            );
            for (const control of controls) {
                const disabled = control.disabled ||
                    control.getAttribute('aria-disabled') === 'true';
                const checked = control.checked ||
                    control.getAttribute('aria-checked') === 'true';
                if (!disabled && !checked) control.click();
            }
            """
        )
    except Exception:
        for checkbox in find_column_checkboxes(modal):
            checkpoint(cancel)
            select_column_checkbox(checkbox)


def wait_booking_report_ready(
    tab: Any, cancel: Event | None, timeout: int = BOOKING_REPORT_TIMEOUT
) -> None:
    """Wait until the asynchronous Booking report is populated."""

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        checkpoint(cancel)
        try:
            row_count = int(
                run_booking_js(tab, BOOKING_REPORT_ROW_COUNT_SCRIPT, cancel) or 0
            )
        except BookingPageRefreshed:
            row_count = 0
        except (TypeError, ValueError, RuntimeError):
            row_count = 0
        if row_count:
            sleep(BOOKING_REPORT_SETTLE_SECONDS)
            return
        sleep(POLL_INTERVAL)
    raise RuntimeError("Relatório de reservas da Booking não terminou de carregar.")


def run_booking_js(tab: Any, script: str, cancel: Event | None) -> Any:
    """Execute JavaScript and expose page refresh as a recoverable event."""

    checkpoint(cancel)
    try:
        return tab.run_js(script)
    except ContextLostError as error:
        raise BookingPageRefreshed(
            "A página da Booking foi atualizada durante a operação."
        ) from error


def open_columns_panel(
    tab: Any, cancel: Event | None, timeout: int = 30
) -> None:
    """Open the panel through the exact XPath without Drission DOM search."""

    deadline = monotonic() + timeout
    last_status = "not-found"
    while monotonic() < deadline:
        checkpoint(cancel)
        try:
            last_status = str(run_booking_js(tab, OPEN_COLUMNS_PANEL_SCRIPT, cancel))
        except BookingPageRefreshed:
            raise
        except RuntimeError as error:
            last_status = type(error).__name__
        if last_status == "clicked":
            sleep(PAGE_SETTLE_SECONDS)
            return
        sleep(POLL_INTERVAL)
    raise RuntimeError(
        "Botão de seleção das colunas da Booking não ficou disponível "
        f"({last_status})."
    )


def configure_columns(tab: Any, cancel: Event | None, timeout: int = 30) -> int:
    """Select the panel checkboxes and apply them without a fragile modal XPath."""

    deadline = monotonic() + timeout
    last_reason = "painel ainda não renderizado"
    while monotonic() < deadline:
        checkpoint(cancel)
        try:
            result = run_booking_js(tab, COLUMN_SELECTION_SCRIPT, cancel)
        except BookingPageRefreshed:
            raise
        except Exception as error:
            last_reason = type(error).__name__
            sleep(POLL_INTERVAL)
            continue
        if isinstance(result, dict):
            last_reason = str(result.get("reason", last_reason))
            if result.get("ready"):
                sleep(ACTION_SETTLE_SECONDS)
                if run_booking_js(tab, APPLY_COLUMNS_SCRIPT, cancel):
                    return int(result.get("changed", 0))
        sleep(POLL_INTERVAL)
    raise RuntimeError(
        "Painel de colunas da Booking não ficou pronto para aplicar "
        f"({last_reason})."
    )


def select_all_booking_rows(tab: Any, cancel: Event | None) -> int | None:
    checkpoint(cancel)
    value = run_booking_js(tab, SELECT_ALL_BOOKING_ROWS_SCRIPT, cancel)
    if value is None:
        raise RuntimeError("Seletor de quantidade de reservas não encontrado.")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def wait_for_booking_data(
    tab: Any,
    page_capacity: int | None,
    cancel: Event | None,
    *,
    clock: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] = sleep,
) -> tuple[list[str], list[list[str]]]:
    """Wait for stable data and extract it without Drission element searches."""

    deadline = clock() + TABLE_TIMEOUT
    previous_signature: tuple[tuple[str, ...], ...] | None = None
    stable_since = clock()
    while clock() < deadline:
        checkpoint(cancel)
        values = run_booking_js(tab, BOOKING_TABLE_SNAPSHOT_SCRIPT, cancel)
        if not isinstance(values, list) or len(values) != 2:
            sleeper(POLL_INTERVAL)
            continue
        raw_headers, raw_rows = values
        headers = [str(header).strip() for header in raw_headers]
        rows = [
            [str(value).replace("\xa0", " ") for value in row]
            for row in raw_rows
        ]
        signature = tuple(tuple(row) for row in rows)
        if rows and page_capacity is not None and len(rows) >= page_capacity:
            return headers, rows
        if rows and signature == previous_signature:
            if clock() - stable_since >= TABLE_STABLE_SECONDS:
                return headers, rows
        else:
            previous_signature = signature
            stable_since = clock()
        sleeper(POLL_INTERVAL)
    raise RuntimeError("A tabela de reservas não estabilizou dentro do prazo.")


def booking_table(browser: Chromium, cancel: Event | None) -> tuple[list[str], list[list[str]]]:
    for _attempt in range(BOOKING_REFRESH_RETRIES):
        tab = browser.latest_tab
        try:
            wait_booking_report_ready(tab, cancel)
            open_columns_panel(tab, cancel)
            configure_columns(tab, cancel)
            page_capacity = select_all_booking_rows(tab, cancel)
            headers, records = wait_for_booking_data(tab, page_capacity, cancel)
        except BookingPageRefreshed:
            sleep(PAGE_SETTLE_SECONDS)
            continue
        if not headers or not records:
            raise RuntimeError("A Booking não retornou reservas.")
        return headers, records
    raise RuntimeError(
        "A página da Booking foi atualizada repetidamente durante a extração."
    )


def wait_for_booking_rows(tab: Any, page_capacity: int | None, cancel: Event | None, *, clock: Callable[[], float] = monotonic, sleeper: Callable[[float], None] = sleep) -> tuple[Any, list[Any]]:
    deadline = clock() + TABLE_TIMEOUT
    previous_signature: tuple[str, ...] | None = None
    stable_since = clock()
    while clock() < deadline:
        checkpoint(cancel)
        table = tab.ele("css:table", timeout=5)
        if not table:
            sleeper(POLL_INTERVAL)
            continue
        rows = table.eles("css:tbody > tr")
        signature = booking_rows_signature(table, rows)
        if rows and page_capacity is not None and len(rows) >= page_capacity:
            return table, rows
        if rows and signature == previous_signature:
            if clock() - stable_since >= TABLE_STABLE_SECONDS:
                return table, rows
        else:
            previous_signature = signature
            stable_since = clock()
        sleeper(POLL_INTERVAL)
    raise RuntimeError("A tabela de reservas não estabilizou dentro do prazo.")


def booking_rows_signature(table: Any, fallback_rows: list[Any]) -> tuple[str, ...]:
    try:
        values = table.run_js(
            """
            return Array.from(this.querySelectorAll('tbody > tr'))
                .map(row => (row.innerText || '').replace(/\u00a0/g, ' ').trim());
            """
        )
        if isinstance(values, list):
            return tuple(str(value) for value in values)
    except (AttributeError, TypeError, RuntimeError):
        pass
    return tuple(row.text for row in fallback_rows)


def booking_row_values(row: Any) -> list[str]:
    values = []
    for cell in row.eles("tag:td"):
        checkbox = cell.ele('css:input[type="checkbox"]', timeout=0)
        value = ("Sim" if checkbox.states.is_checked else "Não") if checkbox else cell.text.strip()
        values.append(value.replace("\xa0", " "))
    return values


def extract_booking_table(
    table: Any, fallback_rows: list[Any]
) -> tuple[list[str], list[list[str]]]:
    try:
        values = table.run_js(
            """
            const clean = value => (value || '').replace(/\u00a0/g, ' ').trim();
            const headers = Array.from(this.querySelectorAll('thead th'))
                .map(cell => clean(cell.innerText));
            const rows = Array.from(this.querySelectorAll('tbody > tr')).map(row =>
                Array.from(row.querySelectorAll('td')).map(cell => {
                    const checkbox = cell.querySelector('input[type="checkbox"]');
                    return checkbox ? (checkbox.checked ? 'Sim' : 'Não')
                        : clean(cell.innerText);
                })
            );
            return [headers, rows];
            """
        )
        headers, records = values
        if headers and records:
            return (
                [str(header).strip() for header in headers],
                [[str(value).replace("\xa0", " ") for value in row] for row in records],
            )
    except (TypeError, ValueError, RuntimeError):
        pass
    headers = [header.text.strip() for header in table.eles("css:thead th")]
    return headers, [booking_row_values(row) for row in fallback_rows]


def login_opera(browser: Chromium, config: BookingConfig, cancel: Event | None) -> Any:
    tab = browser.latest_tab
    tab.get(config.opera_url)
    tab.wait.doc_loaded(timeout=60)
    sleep(PAGE_SETTLE_SECONDS)
    for selector, value, field_name in ((USERNAME_SELECTOR, config.opera_username, "usuário"), (PASSWORD_SELECTOR, config.opera_password, "senha")):
        checkpoint(cancel)
        field = tab.ele(selector, timeout=60)
        if not field:
            raise RuntimeError(f"Campo de {field_name} do OPERA não encontrado.")
        field.input(value, clear=True)
        sleep(ACTION_SETTLE_SECONDS)
    login_button = tab.ele(LOGIN_BUTTON_SELECTOR, timeout=30)
    if not login_button:
        raise RuntimeError("Botão de login do OPERA não encontrado.")
    login_button.click()
    sleep(PAGE_SETTLE_SECONDS)
    tab.wait.doc_loaded(timeout=60)
    continue_button = tab.ele(CONTINUE_BUTTON_SELECTOR, timeout=10)
    if continue_button:
        continue_button.scroll.to_see()
        sleep(ACTION_SETTLE_SECONDS)
        continue_button.click()
        sleep(PAGE_SETTLE_SECONDS)
        tab.wait.doc_loaded(timeout=60)
    find_visible(
        tab,
        PROFILE_SELECTOR,
        "Página inicial do OPERA",
        cancel,
        timeout=120,
    )
    return tab


def open_hotel_search(tab: Any, cancel: Event | None) -> Any:
    """Open OPERA's dynamic location panel and return its fresh input."""

    last_error: RuntimeError | None = None
    for _attempt in range(OPERA_QUERY_RETRIES):
        checkpoint(cancel)
        try:
            click_visible(
                tab,
                PROFILE_SELECTOR,
                "Perfil do OPERA",
                cancel,
                settle_seconds=PAGE_SETTLE_SECONDS,
                timeout=45,
            )
            click_visible(
                tab,
                CHANGE_LOCATION_SELECTOR,
                "Alteração de localização",
                cancel,
                settle_seconds=PAGE_SETTLE_SECONDS,
                timeout=30,
            )
            return find_visible_any(
                tab,
                HOTEL_INPUT_SELECTORS,
                "Campo de pesquisa do hotel/resort",
                cancel,
                timeout=20,
            )
        except RuntimeError as error:
            last_error = error
            sleep(OPERA_ACTION_SETTLE_SECONDS)
    raise RuntimeError(
        "Janela de pesquisa do hotel/resort não abriu após "
        f"{OPERA_QUERY_RETRIES} tentativas: {last_error}"
    )


def select_hotel(tab: Any, hotel_name: str, cancel: Event | None) -> None:
    """Select the OPERA location before opening the reservation workflow."""

    field = open_hotel_search(tab, cancel)
    field.input(hotel_name.strip(), clear=True)
    sleep(OPERA_ACTION_SETTLE_SECONDS)
    click_visible_any(
        tab,
        HOTEL_SEARCH_SELECTORS,
        "Pesquisa do hotel/resort",
        cancel,
        settle_seconds=RESULT_SETTLE_SECONDS,
    )
    click_visible_any(
        tab,
        HOTEL_RESULT_SELECTORS,
        "Hotel/resort pesquisado",
        cancel,
        settle_seconds=PAGE_SETTLE_SECONDS,
    )
    tab.wait.doc_loaded(timeout=60)


def open_reservations(tab: Any, cancel: Event | None) -> None:
    click_visible(tab, BOOKINGS_SELECTOR, "Menu Bookings", cancel)
    if not tab.wait.ele_displayed(RESERVATIONS_SELECTOR, timeout=30):
        raise RuntimeError("Menu Reservations não ficou visível.")
    click_visible(tab, RESERVATIONS_SELECTOR, "Menu Reservations", cancel)
    if not tab.wait.ele_displayed(MANAGE_RESERVATION_SELECTOR, timeout=30):
        raise RuntimeError("Opção Manage Reservation não ficou visível.")
    click_visible(tab, MANAGE_RESERVATION_SELECTOR, "Manage Reservation", cancel)
    tab.wait.doc_loaded(timeout=60)
    click_visible(tab, SEARCH_MODE_SELECTOR, "Busca simplificada", cancel)


def wait_for_text(
    element: Any,
    description: str,
    cancel: Event | None,
    timeout: int = 15,
) -> str:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        checkpoint(cancel)
        text = str(element.text or "").strip()
        if text:
            return text
        sleep(POLL_INTERVAL)
    raise RuntimeError(f"{description} não carregou conteúdo dentro do prazo.")


def find_visible_now(tab: Any, selector: str) -> Any | None:
    """Return a fresh visible element without waiting."""

    try:
        elements = tab.eles(selector, timeout=0)
    except TRANSIENT_BROWSER_ERRORS:
        return None
    for element in elements:
        try:
            if element.states.is_displayed:
                return element
        except TRANSIENT_BROWSER_ERRORS:
            continue
    return None


def click_opera_dynamic(
    tab: Any,
    selector: str,
    description: str,
    cancel: Event | None,
    *,
    settle_seconds: float = 0,
    timeout: int = 30,
) -> None:
    """Click a dynamic OPERA control without an unconditional scroll delay."""

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        checkpoint(cancel)
        try:
            element = find_visible(
                tab, selector, description, cancel, timeout=5
            )
            try:
                element.click()
            except NoRectError:
                element.click(by_js=True)
            if settle_seconds:
                sleep(settle_seconds)
            return
        except TRANSIENT_BROWSER_ERRORS:
            sleep(POLL_INTERVAL)
    raise RuntimeError(f"{description} não respondeu ao clique.")


def click_opera_dynamic_any(
    tab: Any,
    selectors: tuple[str, ...],
    description: str,
    cancel: Event | None,
    *,
    settle_seconds: float = 0,
    timeout: int = 15,
) -> None:
    """Click the first visible dynamic OPERA control without implicit waits."""

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        checkpoint(cancel)
        for selector in selectors:
            element = find_visible_now(tab, selector)
            if element is None:
                continue
            try:
                element.click()
            except NoRectError:
                element.click(by_js=True)
            if settle_seconds:
                sleep(settle_seconds)
            return
        sleep(POLL_INTERVAL)
    raise RuntimeError(f"{description} não respondeu ao clique.")


def opera_result_signature(tab: Any) -> str:
    """Return the visible result row contents without retaining DOM elements."""

    try:
        return str(tab.run_js(OPERA_RESULT_SIGNATURE_SCRIPT) or "").strip()
    except (AttributeError, *TRANSIENT_BROWSER_ERRORS):
        return ""


def wait_for_new_opera_result(
    tab: Any,
    previous: Any | None,
    cancel: Event | None,
    previous_signature: str = "",
    timeout: int = OPERA_RESULT_TIMEOUT,
) -> Any:
    """Wait until OPERA replaces the element or changes the result row."""

    deadline = monotonic() + timeout
    previous_invalidated = previous is None
    while monotonic() < deadline:
        checkpoint(cancel)
        current = find_visible_now(tab, RATE_LINK_SELECTOR)
        current_signature = opera_result_signature(tab) if current else ""
        if (
            current is not None
            and current_signature
            and current_signature != previous_signature
        ):
            return current
        if not previous_invalidated:
            try:
                previous_invalidated = (
                    not previous.states.is_alive
                    or not previous.states.is_displayed
                )
            except TRANSIENT_BROWSER_ERRORS:
                previous_invalidated = True
        if previous_invalidated:
            if current is not None:
                return current
        sleep(POLL_INTERVAL)
    raise RuntimeError("O resultado da nova reserva não terminou de carregar.")


def wait_for_opera_text(
    tab: Any,
    selector: str,
    description: str,
    cancel: Event | None,
    timeout: int = OPERA_RESULT_TIMEOUT,
) -> str:
    """Read dynamic OPERA text while reacquiring replaced elements."""

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        checkpoint(cancel)
        element = find_visible_now(tab, selector)
        if element is not None:
            try:
                text = str(element.text or "").strip()
            except TRANSIENT_BROWSER_ERRORS:
                text = ""
            if text:
                return text
        sleep(POLL_INTERVAL)
    raise RuntimeError(f"{description} não carregou conteúdo dentro do prazo.")


def recover_opera_search(tab: Any, cancel: Event | None) -> None:
    """Best-effort return to the reservation search after a partial refresh."""

    checkpoint(cancel)
    try:
        click_opera_dynamic_any(
            tab,
            CLOSE_RATE_SELECTORS,
            "Fechar detalhes da tarifa",
            cancel,
            timeout=2,
        )
    except (RuntimeError, *TRANSIENT_BROWSER_ERRORS):
        pass
    sleep(OPERA_ACTION_SETTLE_SECONDS)
    try:
        find_visible(
            tab,
            RESERVATION_INPUT_SELECTOR,
            "Campo de pesquisa da reserva",
            cancel,
            timeout=15,
        )
    except RuntimeError:
        pass


def fill_opera_reservation(field: Any, reservation: str) -> None:
    """Replace the search value and commit it without typing control characters."""

    field.input(reservation, clear=True)
    field.run_js("this.blur();")


def _opera_total_once(
    tab: Any, reservation: str, cancel: Event | None
) -> str:
    checkpoint(cancel)
    previous_rate = find_visible_now(tab, RATE_LINK_SELECTOR)
    previous_signature = opera_result_signature(tab) if previous_rate else ""
    field = find_visible(
        tab,
        RESERVATION_INPUT_SELECTOR,
        "Campo de pesquisa da reserva",
        cancel,
        timeout=30,
    )
    fill_opera_reservation(field, reservation)
    sleep(OPERA_INPUT_SETTLE_SECONDS)
    click_opera_dynamic(
        tab,
        SEARCH_BUTTON_SELECTOR,
        "Botão de pesquisa",
        cancel,
        settle_seconds=0,
    )
    rate_link = wait_for_new_opera_result(
        tab,
        previous_rate,
        cancel,
        previous_signature=previous_signature,
    )
    try:
        rate_link.click()
    except NoRectError:
        rate_link.click(by_js=True)
    sleep(OPERA_DETAIL_SETTLE_SECONDS)
    total = wait_for_opera_text(
        tab,
        TOTAL_VALUE_SELECTOR,
        "Valor total da reserva",
        cancel,
    )
    click_opera_dynamic_any(
        tab,
        CLOSE_RATE_SELECTORS,
        "Fechar detalhes da tarifa",
        cancel,
        settle_seconds=OPERA_CLOSE_SETTLE_SECONDS,
    )
    find_visible(
        tab,
        RESERVATION_INPUT_SELECTOR,
        "Campo para uma nova consulta",
        cancel,
        timeout=30,
    )
    sleep(OPERA_BETWEEN_QUERIES_SECONDS)
    return total


def opera_total(tab: Any, reservation: str, cancel: Event | None) -> str:
    last_error: Exception | None = None
    for _attempt in range(OPERA_QUERY_RETRIES):
        try:
            return _opera_total_once(tab, reservation, cancel)
        except AutomationCancelled:
            raise
        except OPERA_RETRYABLE_ERRORS as error:
            last_error = error
            recover_opera_search(tab, cancel)
    raise RuntimeError(
        f"OPERA não estabilizou após {OPERA_QUERY_RETRIES} tentativas: "
        f"{last_error}"
    )
