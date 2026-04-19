"""``armillary share`` / ``card`` / ``pulse`` — portfolio export commands.

Extracted from ``cli_tools.py`` to keep each module under the 400-line
architecture target.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from armillary.cli import app


@app.command("share")
def share_command(
    tweet: bool = typer.Option(False, "--tweet", help="Generate tweet template."),
    hn: bool = typer.Option(False, "--hn", help="Generate Show HN post."),
) -> None:
    """Generate shareable text from your portfolio data."""
    from armillary.share_service import generate_hn_post, generate_tweet

    if not tweet and not hn:
        tweet = True  # default

    console = Console()
    if tweet:
        console.print("\n[bold]Tweet:[/bold]\n")
        console.print(generate_tweet())
    if hn:
        console.print("\n[bold]Show HN:[/bold]\n")
        console.print(generate_hn_post())
    console.print()


@app.command("card")
def card_command(
    output: str = typer.Option(
        "armillary-card.html",
        "--output",
        "-o",
        help="Output file path.",
    ),
) -> None:
    """Export your activity heatmap as a shareable HTML card."""
    from armillary.heatmap_service import (
        daily_activity,
        export_heatmap_html,
        heatmap_summary,
    )

    activity = daily_activity()
    summary = heatmap_summary(activity)
    html = export_heatmap_html(activity, summary)

    Path(output).write_text(html, encoding="utf-8")
    typer.secho(f"Card exported to {output}", fg=typer.colors.GREEN)


@app.command("pulse")
def pulse_command() -> None:
    """Weekly pulse — what changed across your projects this week."""
    from armillary.pulse_service import (
        format_pulse,
        generate_pulse,
        load_history,
    )

    pulse = generate_pulse()
    console = Console()
    console.print(f"\n{format_pulse(pulse)}")

    history = load_history()
    if len(history) >= 2:
        console.print(
            f"\n[dim]History: {len(history)} weeks tracked. "
            f"Active: {' → '.join(str(h['active']) for h in history[-4:])}[/dim]"
        )
    console.print()
