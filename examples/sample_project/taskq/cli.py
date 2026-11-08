"""Command-line interface."""

import click

from .client import TaskClient


@click.group()
def cli():
    """taskq command-line tool."""


@cli.command("submit")
@click.argument("name")
@click.option("--priority", default="normal", help="Task priority.")
@click.option("--wait", is_flag=True, help="Block until the task completes.")
def submit_cmd(name, priority, wait):
    """Submit a task from the shell."""
    client = TaskClient("http://localhost:8000")
    task_id = client.submit(name, {}, priority=priority)
    if wait:
        client.wait(task_id)
    click.echo(task_id)


@cli.command("worker")
@click.option("--concurrency", default=4, help="Number of worker processes.")
def worker_cmd(concurrency):
    """Start a worker pool."""
    click.echo(f"starting {concurrency} workers")
