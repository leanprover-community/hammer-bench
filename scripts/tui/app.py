"""Main TUI application for hammer-bench."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Static

from .data import load_runs_hierarchical
from .widgets import RunTree, ComparisonPanel, RunSelected


class BenchApp(App):
    """Hammer-bench TUI application."""

    TITLE = "Hammer-Bench"

    CSS = """
    #main-container {
        width: 100%;
        height: 100%;
    }

    #left-panel {
        width: 40%;
        height: 100%;
        border-right: solid $border;
    }

    #right-panel {
        width: 60%;
        height: 100%;
    }

    .panel-title {
        text-style: bold;
        background: $surface;
        padding: 0 1;
        height: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.runs = load_runs_hierarchical()

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-container"):
            with Vertical(id="left-panel"):
                yield Static("Runs", classes="panel-title")
                yield RunTree(self.runs, id="run-tree")
            with Vertical(id="right-panel"):
                yield Static("Comparison", classes="panel-title")
                yield ComparisonPanel(id="comparison-panel")
        yield Footer()

    def on_run_selected(self, message: RunSelected) -> None:
        """Handle run selection changes."""
        panel = self.query_one("#comparison-panel", ComparisonPanel)
        panel.update_selection(message.selected_run_ids)

    def action_refresh(self) -> None:
        """Refresh the runs data."""
        self.runs = load_runs_hierarchical()
        tree = self.query_one("#run-tree", RunTree)
        tree.refresh_data(self.runs)
        self.notify("Refreshed")


def run_tui() -> int:
    """Run the TUI application."""
    app = BenchApp()
    app.run()
    return 0
