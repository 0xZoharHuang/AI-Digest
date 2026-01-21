"""Markdown export module for saving reports locally."""

from pathlib import Path

from rich.console import Console

from src.integrator.report_generator import DailyReport, ReportGenerator

console = Console()


class MarkdownExporter:
    """Export daily reports to local Markdown files."""

    def __init__(self):
        self.report_generator = ReportGenerator()

    def export(
        self,
        report: DailyReport,
        output_dir: str | Path = "data/reports"
    ) -> Path:
        """
        Export a report to a Markdown file.

        Args:
            report: DailyReport to export
            output_dir: Directory to save the report

        Returns:
            Path to the exported file
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Generate filename from date
        filename = f"{report.date}.md"
        file_path = output_path / filename

        console.print(f"[blue]Exporting report to {file_path}...[/blue]")

        # Generate markdown content
        markdown_content = self.report_generator.to_markdown(report)

        # Write to file
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        console.print(f"[green]Report exported to {file_path}[/green]")
        return file_path

    def export_with_backup(
        self,
        report: DailyReport,
        output_dir: str | Path = "data/reports"
    ) -> Path:
        """
        Export a report, backing up existing file if present.

        Args:
            report: DailyReport to export
            output_dir: Directory to save the report

        Returns:
            Path to the exported file
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        filename = f"{report.date}.md"
        file_path = output_path / filename

        # Backup existing file if present
        if file_path.exists():
            backup_path = output_path / f"{report.date}.backup.md"
            file_path.rename(backup_path)
            console.print(f"[yellow]Backed up existing file to {backup_path}[/yellow]")

        return self.export(report, output_dir)
