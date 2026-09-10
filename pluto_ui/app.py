from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import date
from pathlib import Path
from threading import Event

import flet as ft

from automations import booking, recebimentos

BLUE = "#4C7DFF"
BACKGROUND = "#0C1017"
SIDEBAR = "#111722"
CARD = "#171F2C"
BORDER = "#283448"
TEXT = "#F4F7FB"
MUTED = "#9BAAC0"
GREEN = "#31B77A"
RED = "#E05D68"
YELLOW = "#E0A83E"
LOGGER = logging.getLogger("pluto")


def message(page: ft.Page, text: str, error: bool = False) -> None:
    page.show_dialog(ft.SnackBar(text, bgcolor=RED if error else GREEN, show_close_icon=True))


def title(name: str, description: str) -> ft.Control:
    return ft.Column([
        ft.Text(name, size=26, weight=ft.FontWeight.BOLD, color=TEXT),
        ft.Text(description, color=MUTED),
    ], spacing=4)


def metric(label: str, value: ft.Text, color: str) -> ft.Control:
    return ft.Container(
        ft.Column([ft.Text(label, size=11, color=MUTED), value], spacing=3),
        bgcolor=CARD,
        border_radius=12,
        padding=14,
        expand=True,
        border=ft.Border(left=ft.BorderSide(3, color), top=ft.BorderSide(1, BORDER), right=ft.BorderSide(1, BORDER), bottom=ft.BorderSide(1, BORDER)),
    )


def table_container(columns: list[str], values: list[list[str]]) -> ft.Control:
    data = ft.DataTable(
        columns=[ft.DataColumn(ft.Text(column, weight=ft.FontWeight.W_600)) for column in columns],
        rows=[
            ft.DataRow(cells=[ft.DataCell(ft.Text(value, size=12, max_lines=2)) for value in row])
            for row in values
        ],
        border=ft.Border.all(1, BORDER),
        border_radius=10,
        heading_row_color="#202B3B",
        data_row_min_height=42,
        data_row_max_height=56,
        expand=True,
    )
    return ft.Container(
        ft.Column(
            [ft.Row([data], scroll=ft.ScrollMode.AUTO)],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )


class ReceiptsView:
    def __init__(self, page: ft.Page, set_status):
        self.page = page
        self.set_status = set_status
        self.result: recebimentos.ReconciliationResult | None = None
        self.table = ft.Container(expand=True)
        self.files = ft.Text("Selecione os relatórios OPERA, Rede e CMFlex.", color=MUTED, size=12)
        self.total = ft.Text("—", size=20, weight=ft.FontWeight.BOLD)
        self.ok = ft.Text("—", size=20, weight=ft.FontWeight.BOLD)
        self.divergent = ft.Text("—", size=20, weight=ft.FontWeight.BOLD)
        self.ignored = ft.Text("—", size=20, weight=ft.FontWeight.BOLD)
        self.export_format = ft.Dropdown(
            value="Excel", width=130, dense=True, label="Formato",
            options=[ft.DropdownOption(key=value, text=value) for value in ("Excel", "CSV")],
        )
        self.select_button = ft.FilledButton("Selecionar arquivos", icon=ft.Icons.UPLOAD_FILE, bgcolor=BLUE, on_click=self.select_files)
        self.export_button = ft.Button("Exportar", icon=ft.Icons.DOWNLOAD, disabled=True, on_click=self.export)
        self.control = self.build()

    def build(self) -> ft.Control:
        return ft.Column([
            title("Conferência de Recebimentos", "Concilia OPERA, Rede e CMFlex nos dois sentidos."),
            ft.Container(
                ft.Row([ft.Icon(ft.Icons.FOLDER_OPEN, color=BLUE), ft.Column([ft.Text("Arquivos de origem", weight=ft.FontWeight.BOLD), self.files], spacing=2), self.select_button], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                bgcolor=CARD, border=ft.Border.all(1, BORDER), border_radius=12, padding=14,
            ),
            ft.Row([
                metric("Registros", self.total, BLUE), metric("Conciliados", self.ok, GREEN),
                metric("Divergentes", self.divergent, RED), metric("Ignorados", self.ignored, YELLOW),
            ]),
            ft.Row([ft.Text("Resultado", size=17, weight=ft.FontWeight.BOLD), self.export_format, self.export_button], alignment=ft.MainAxisAlignment.END),
            self.table,
        ], spacing=14, expand=True)

    async def select_files(self, _event=None) -> None:
        if self.page.web:
            message(self.page, "A conferência de arquivos deve ser executada no aplicativo desktop.", True)
            return
        picker = ft.FilePicker()
        selected = await picker.pick_files(
            dialog_title="Relatórios de recebimentos", allow_multiple=True,
            file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["xml", "xlsx"],
        )
        if not selected:
            return
        paths = [Path(item.path) for item in selected if item.path]
        self.select_button.disabled = True
        self.export_button.disabled = True
        self.set_status("Processando os relatórios...", None)
        self.page.update()
        try:
            self.result = await asyncio.to_thread(recebimentos.analyze, paths)
        except Exception as error:
            LOGGER.exception("Falha na conferência de recebimentos")
            message(self.page, str(error), True)
            self.set_status("Falha no processamento", 0)
        else:
            self.files.value = " | ".join(path.name for path in paths)
            self.total.value = str(len(self.result.rows))
            self.ok.value = str(self.result.matched_count)
            self.divergent.value = str(self.result.divergent_count)
            self.ignored.value = str(self.result.ignored_rede_count)
            self.export_button.disabled = False
            self.render()
            self.set_status("Conferência de recebimentos concluída", 1)
        finally:
            self.select_button.disabled = False
            self.page.update()

    def render(self) -> None:
        if not self.result:
            return
        rows = sorted(self.result.rows, key=lambda row: (row.status != "DIVERGENTE", row.date or date.min, row.source))
        values = [[
            row.date.strftime("%d/%m/%Y") if row.date else "—", row.source, row.method,
            row.brand or "—", f"R$ {row.amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", "."),
            row.opera_id or "—", row.rede_id or "—", row.cmflex_id or "—", row.status, row.detail,
        ] for row in rows[:250]]
        self.table.content = table_container(["Data", "Origem", "Meio", "Bandeira", "Valor", "OPERA", "Rede", "CMFlex", "Resultado", "Detalhe"], values)

    async def export(self, _event=None) -> None:
        if not self.result:
            return
        extension = "xlsx" if self.export_format.value == "Excel" else "csv"
        picker = ft.FilePicker()
        output = await picker.save_file(
            dialog_title="Exportar conferência", file_name=f"conferencia_recebimentos.{extension}",
            file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=[extension],
        )
        if not output:
            return
        path = Path(output).with_suffix(f".{extension}")
        function = recebimentos.save_excel if extension == "xlsx" else recebimentos.save_csv
        try:
            await asyncio.to_thread(function, self.result, path)
        except Exception as error:
            message(self.page, f"Falha na exportação: {error}", True)
        else:
            self.set_status(f"Resultado exportado: {path.name}", 1)
            message(self.page, "Resultado exportado com sucesso.")
        self.page.update()


class BookingView:
    def __init__(self, page: ft.Page, set_status):
        self.page = page
        self.set_status = set_status
        self.cancel_event: Event | None = None
        self.result: booking.BookingResult | None = None
        self.table = ft.Container(expand=True)
        self.log = ft.Text("Aguardando execução.", color=MUTED, size=12)
        self.ok = ft.Text("—", size=20, weight=ft.FontWeight.BOLD)
        self.divergent = ft.Text("—", size=20, weight=ft.FontWeight.BOLD)
        self.errors = ft.Text("—", size=20, weight=ft.FontWeight.BOLD)
        self.not_compared = ft.Text("—", size=20, weight=ft.FontWeight.BOLD)
        self.total = ft.Text("—", size=20, weight=ft.FontWeight.BOLD)
        self.booking_user = ft.TextField(label="Usuário Booking", dense=True, expand=True)
        self.booking_password = ft.TextField(label="Senha Booking", password=True, can_reveal_password=True, dense=True, expand=True)
        self.opera_user = ft.TextField(label="Usuário OPERA", dense=True, expand=True)
        self.opera_password = ft.TextField(label="Senha OPERA", password=True, can_reveal_password=True, dense=True, expand=True)
        self.hotel = ft.TextField(label="Hotel/Resort no OPERA", dense=True, expand=True)
        self.output = ft.TextField(label="Pasta de saída", value=str(Path.cwd() / "output"), dense=True, expand=True)
        self.run_button = ft.FilledButton("Executar RPA", icon=ft.Icons.PLAY_ARROW, bgcolor=BLUE, on_click=self.run)
        self.cancel_button = ft.Button("Cancelar", icon=ft.Icons.STOP, color=RED, disabled=True, on_click=self.cancel)
        self.result_filter = ft.Dropdown(
            value="Divergências e erros",
            width=210,
            dense=True,
            label="Exibir",
            options=[
                ft.DropdownOption(key=value, text=value)
                for value in (
                    "Divergências e erros",
                    "Todos",
                    "Conciliadas",
                    "Divergências",
                    "Erros",
                    "Não conferidas",
                )
            ],
            on_select=self.change_filter,
        )
        self.export_button = ft.Button(
            "Exportar Excel",
            icon=ft.Icons.DOWNLOAD,
            disabled=True,
            on_click=self.export_excel,
        )
        self.control = self.build()

    def build(self) -> ft.Control:
        return ft.Column([
            title("Conferência Booking × OPERA", "Extrai reservas da Booking, consulta o OPERA e gera a conferência."),
            ft.Container(ft.Column([
                ft.Text("Credenciais", weight=ft.FontWeight.BOLD),
                ft.Row([self.booking_user, self.booking_password]),
                ft.Row([self.opera_user, self.opera_password]),
                ft.Row([self.hotel]),
                ft.Row([self.output, ft.IconButton(ft.Icons.FOLDER_OPEN, tooltip="Escolher pasta", on_click=self.select_output)]),
                ft.Row([self.run_button, self.cancel_button, self.log], alignment=ft.MainAxisAlignment.START),
            ], spacing=10), bgcolor=CARD, border=ft.Border.all(1, BORDER), border_radius=12, padding=14),
            ft.Row([
                metric("Reservas", self.total, BLUE),
                metric("Conciliadas", self.ok, GREEN),
                metric("Divergentes", self.divergent, RED),
                metric("Erros", self.errors, YELLOW),
                metric("Não conferidas", self.not_compared, MUTED),
            ]),
            ft.Row([
                ft.Text("Resultado", size=17, weight=ft.FontWeight.BOLD),
                ft.Row([self.result_filter, self.export_button], spacing=10),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            self.table,
        ], spacing=14, expand=True)

    async def select_output(self, _event=None) -> None:
        picker = ft.FilePicker()
        selected = await picker.get_directory_path(dialog_title="Pasta dos resultados")
        if selected:
            self.output.value = selected
            self.page.update()

    def cancel(self, _event=None) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.log.value = "Cancelamento solicitado; aguardando a etapa atual terminar."
            self.set_status("Cancelando RPA...", None)
            self.page.update()

    async def run(self, _event=None) -> None:
        if self.page.web:
            message(self.page, "O RPA exige o aplicativo desktop e um navegador Chromium local.", True)
            return
        config = booking.BookingConfig(
            self.booking_user.value.strip(), self.booking_password.value,
            self.opera_user.value.strip(), self.opera_password.value,
            Path(self.output.value).expanduser(),
            hotel_name=self.hotel.value.strip(),
        )
        try:
            config.validate()
        except ValueError as error:
            message(self.page, str(error), True)
            return
        self.cancel_event = Event()
        self.run_button.disabled = True
        self.cancel_button.disabled = False
        self.export_button.disabled = True
        loop = asyncio.get_running_loop()
        updates: asyncio.Queue[tuple[str, float]] = asyncio.Queue()

        def progress(text: str, value: float) -> None:
            loop.call_soon_threadsafe(updates.put_nowait, (text, value))

        worker = asyncio.create_task(asyncio.to_thread(booking.run, config, progress, self.cancel_event))
        try:
            while not worker.done():
                try:
                    text, value = await asyncio.wait_for(updates.get(), timeout=0.25)
                    self.log.value = text
                    self.set_status(text, value)
                    self.page.update()
                except TimeoutError:
                    pass
            self.result = await worker
        except booking.AutomationCancelled as error:
            self.log.value = str(error)
            self.set_status("RPA cancelado", 0)
        except Exception as error:
            LOGGER.exception("Falha no RPA Booking")
            self.log.value = str(error)
            self.set_status("Falha no RPA", 0)
            message(self.page, str(error), True)
        else:
            self.total.value = str(len(self.result.rows))
            self.ok.value = str(self.result.matched_count)
            self.divergent.value = str(self.result.mismatch_count)
            self.errors.value = str(self.result.error_count)
            self.not_compared.value = str(self.result.not_compared_count)
            self.log.value = f"Arquivos gerados em {self.result.report_excel.parent}"
            self.export_button.disabled = False
            self.render()
            self.set_status("Conferência Booking × OPERA concluída", 1)
            message(self.page, "RPA concluído com sucesso.")
        finally:
            self.booking_password.value = ""
            self.opera_password.value = ""
            self.run_button.disabled = False
            self.cancel_button.disabled = True
            self.cancel_event = None
            self.page.update()

    def change_filter(self, _event=None) -> None:
        self.render()
        self.page.update()

    async def export_excel(self, _event=None) -> None:
        if not self.result or not self.result.report_excel.exists():
            message(self.page, "O relatório Excel ainda não está disponível.", True)
            return
        picker = ft.FilePicker()
        output = await picker.save_file(
            dialog_title="Exportar conferência Booking × OPERA",
            file_name="conferencia_booking_opera.xlsx",
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["xlsx"],
        )
        if not output:
            return
        destination = Path(output).with_suffix(".xlsx")
        try:
            if destination.resolve() != self.result.report_excel.resolve():
                await asyncio.to_thread(
                    shutil.copy2, self.result.report_excel, destination
                )
        except OSError as error:
            LOGGER.exception("Falha ao exportar relatório Booking")
            message(self.page, f"Falha na exportação: {error}", True)
        else:
            self.set_status(f"Excel exportado: {destination.name}", 1)
            message(self.page, "Excel exportado com sucesso.")
        self.page.update()

    def render(self) -> None:
        if not self.result:
            return
        def find(row: dict[str, str], *prefixes: str) -> str:
            return next((
                value
                for key, value in row.items()
                if any(booking.normalize(key).startswith(prefix) for prefix in prefixes)
            ), "")
        selected_filter = self.result_filter.value

        def visible(row: dict[str, str]) -> bool:
            status = row.get("Conferência", "")
            if selected_filter == "Todos":
                return True
            if selected_filter == "Conciliadas":
                return status == "OK"
            if selected_filter == "Divergências":
                return status == "DIVERGENTE"
            if selected_filter == "Erros":
                return status.startswith("ERRO:")
            if selected_filter == "Não conferidas":
                return status == "NÃO CONFERIDA - REGRA"
            return status == "DIVERGENTE" or status.startswith("ERRO:")

        rows = sorted(
            (row for row in self.result.rows if visible(row)),
            key=lambda row: (
                not row.get("Conferência", "").startswith("ERRO:"),
                row.get("Conferência") != "DIVERGENTE",
                find(row, "numero da reserva", "book number", "booking number"),
            ),
        )
        values = [[
            find(row, "numero da reserva", "book number", "booking number"),
            find(row, "nome do hospede", "guest name"),
            row.get("Itens agrupados", "1"), find(row, "status", "result"),
            row.get("Valor Booking calculado") or find(row, "valor final"),
            row.get("Valor OPERA", ""), row.get("Diferença", ""), row.get("Conferência", ""),
        ] for row in rows]
        self.table.content = table_container(
            ["Reserva", "Hóspede", "Itens", "Status Booking", "Booking", "OPERA", "Diferença", "Resultado"],
            values,
        )


class PlutoApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.content = ft.Container(expand=True)
        self.status = ft.Text("Pronto", color=MUTED, size=12)
        self.progress = ft.ProgressBar(value=0, color=BLUE, bgcolor=BORDER, bar_height=4)
        self.selected = 0
        self.views = [ReceiptsView(page, self.set_status), BookingView(page, self.set_status)]
        self.navigation = ft.Column(spacing=8)

    def build(self) -> None:
        self.page.title = "PLUTO"
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.theme = ft.Theme(color_scheme_seed=BLUE, font_family="Segoe UI")
        self.page.bgcolor = BACKGROUND
        self.page.padding = 0
        self.page.window.width = 1450
        self.page.window.height = 880
        self.page.window.min_width = 1050
        self.page.window.min_height = 700
        self.render_navigation()
        sidebar = ft.Container(ft.Column([
            ft.Container(ft.Row([ft.Icon(ft.Icons.AUTO_AWESOME, color=BLUE), ft.Text("PLUTO", size=20, weight=ft.FontWeight.BOLD)], spacing=10), padding=18),
            ft.Divider(color=BORDER),
            self.navigation,
        ], spacing=8), width=230, bgcolor=SIDEBAR, border=ft.Border(right=ft.BorderSide(1, BORDER)))
        footer = ft.Container(ft.Column([self.status, self.progress], spacing=5), bgcolor=SIDEBAR, padding=ft.Padding(left=18, top=8, right=18, bottom=10))
        self.content.content = self.views[0].control
        body = ft.Row([sidebar, ft.Container(self.content, padding=18, expand=True)], spacing=0, expand=True)
        self.page.add(ft.Column([body, footer], spacing=0, expand=True))

    def nav_item(self, index: int, icon: str, label: str) -> ft.Control:
        return ft.Container(
            ft.Row([ft.Icon(icon, color=BLUE if index == self.selected else MUTED), ft.Text(label, weight=ft.FontWeight.W_600)], spacing=12),
            padding=ft.Padding(left=18, top=12, right=12, bottom=12), margin=ft.Margin(left=8, top=0, right=8, bottom=0),
            bgcolor="#1B2B48" if index == self.selected else ft.Colors.TRANSPARENT, border_radius=10,
            on_click=lambda _event, selected=index: self.show(selected),
        )

    def show(self, index: int) -> None:
        self.selected = index
        self.content.content = self.views[index].control
        self.render_navigation()
        self.set_status("Atividade carregada", 0)
        self.page.update()

    def render_navigation(self) -> None:
        self.navigation.controls = [
            self.nav_item(0, ft.Icons.ACCOUNT_BALANCE_WALLET, "Recebimentos"),
            self.nav_item(1, ft.Icons.TRAVEL_EXPLORE, "Booking × OPERA"),
        ]

    def set_status(self, text: str, value: float | None) -> None:
        self.status.value = text
        self.progress.value = value


def main(page: ft.Page) -> None:
    PlutoApp(page).build()
