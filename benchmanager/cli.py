"""CLI(§12)。

エンジンを直接呼ぶだけの薄い皮に留める(Phase 3 で GUI / Agent を載せる際、
この層を差し替えるだけで済むようにするため)。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Optional

import typer

from . import const
from .config.loader import load_config
from .drivers.registry import available_tools, get_driver_class
from .engine import Engine, EngineOptions
from .errors import BenchmanError
from .i18n import set_lang, t
from .logging_setup import setup_logging
from .report.html import default_output_name, write_report
from .store.importer import import_results
from .store.store import Store

app = typer.Typer(add_completion=False, no_args_is_help=True, help=t("cli.app_help"))
config_app = typer.Typer(no_args_is_help=True, help=t("cli.config_help"))
app.add_typer(config_app, name="config")

ConfigDirArg = Annotated[
    Optional[Path], typer.Argument(help=t("cli.opt_config_dir"), show_default=False)
]
TestConfigOpt = Annotated[Optional[Path], typer.Option("--test-config", help=t("cli.opt_test_config"))]
ServersConfigOpt = Annotated[
    Optional[Path], typer.Option("--servers-config", help=t("cli.opt_servers_config"))
]
StorageConfigOpt = Annotated[
    Optional[Path], typer.Option("--storage-config", help=t("cli.opt_storage_config"))
]
DbOpt = Annotated[Path, typer.Option("--db", help=t("cli.opt_db"))]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help=t("cli.opt_verbose"))]
LangOpt = Annotated[Optional[str], typer.Option("--lang", help=t("cli.opt_lang"))]


def _fail(exc: Exception) -> None:
    typer.secho(f"{t('cli.error_prefix')}{exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _load(
    config_dir: Path | None,
    test_config: Path | None,
    servers_config: Path | None,
    storage_config: Path | None,
):
    return load_config(
        config_dir,
        test_path=test_config,
        servers_path=servers_config,
        storage_path=storage_config,
    )


@app.callback()
def main_callback(lang: LangOpt = None) -> None:
    """共通オプション。"""
    if lang:
        set_lang(lang)


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


@app.command(help=t("cli.run_help"))
def run(
    config_dir: ConfigDirArg = None,
    test_config: TestConfigOpt = None,
    servers_config: ServersConfigOpt = None,
    storage_config: StorageConfigOpt = None,
    results_dir: Annotated[
        Path, typer.Option("--results-dir", help=t("cli.opt_results_dir"))
    ] = Path(const.DEFAULT_RESULTS_DIRNAME),
    db: DbOpt = Path(const.DEFAULT_DB_FILENAME),
    fail_fast: Annotated[bool, typer.Option("--fail-fast", help=t("cli.opt_fail_fast"))] = False,
    report: Annotated[
        Optional[Path], typer.Option("--report", "-r", help=t("cli.opt_output"))
    ] = None,
    verbose: VerboseOpt = False,
) -> None:
    setup_logging(verbose)
    try:
        config = _load(config_dir, test_config, servers_config, storage_config)
        engine = Engine(
            config,
            EngineOptions(results_root=results_dir, db_path=db, fail_fast=fail_fast),
        )
        result = asyncio.run(engine.run())
        if report is not None:
            with Store(db) as store:
                suite = store.load_suite(result.suite_name)
                if suite is not None:
                    path = write_report(suite, report)
                    typer.echo(t("report.written", path=path))
    except BenchmanError as exc:
        _fail(exc)


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------


@app.command(help=t("cli.plan_help"))
def plan(
    config_dir: ConfigDirArg = None,
    test_config: TestConfigOpt = None,
    servers_config: ServersConfigOpt = None,
    storage_config: StorageConfigOpt = None,
    verbose: VerboseOpt = False,
) -> None:
    setup_logging(verbose, log_file=None)
    try:
        config = _load(config_dir, test_config, servers_config, storage_config)
        info = Engine(config).plan()
    except BenchmanError as exc:
        _fail(exc)
        return

    typer.echo(t("plan.header", suite=info.suite_name, tool=info.tool, strategy=info.strategy))
    if info.env_order:
        typer.echo(t("plan.env_order", order=" -> ".join(info.env_order)))
    else:
        typer.echo(t("plan.env_order_none"))
    typer.echo(t("plan.conditions_header", count=len(info.points)))
    for point in info.points:
        typer.echo(t("plan.condition_line", index=point.index, conditions=point.sweep_label()))
    typer.echo(t("plan.switch_count", count=info.env_switches, naive=info.env_switches_naive))
    typer.echo(t("plan.points_per_run", points=info.points_per_run))
    total = info.total_seconds
    typer.echo(
        t(
            "plan.estimated",
            duration=f"{total // 3600}h {total % 3600 // 60}m {total % 60}s",
            per_point=info.seconds_per_point,
            points=len(info.points) * info.points_per_run,
        )
    )
    typer.echo(t("plan.estimated_note"))


# --------------------------------------------------------------------------
# config validate
# --------------------------------------------------------------------------


@config_app.command("validate", help=t("cli.validate_help"))
def config_validate(
    config_dir: ConfigDirArg = None,
    test_config: TestConfigOpt = None,
    servers_config: ServersConfigOpt = None,
    storage_config: StorageConfigOpt = None,
) -> None:
    try:
        config = _load(config_dir, test_config, servers_config, storage_config)
    except BenchmanError as exc:
        _fail(exc)
        return
    location = config.paths.config_dir or config.paths.test.parent
    typer.secho(t("config.ok", path=location), fg=typer.colors.GREEN)
    typer.echo(t("config.ok_files", files=", ".join(p.name for p in config.paths.all())))


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------


@app.command("import", help=t("cli.import_help"))
def import_cmd(
    results_dir: Annotated[Path, typer.Argument(help=t("cli.opt_results_dir"))],
    db: DbOpt = Path(const.DEFAULT_DB_FILENAME),
    verbose: VerboseOpt = False,
) -> None:
    setup_logging(verbose, log_file=None)
    try:
        with Store(db) as store:
            # 完了件数は importer 側がログに出す(二重表示を避ける)
            import_results(results_dir, store)
    except BenchmanError as exc:
        _fail(exc)


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


@app.command(help=t("cli.report_help"))
def report(
    test_suite: Annotated[str, typer.Argument(help="test_suite")],
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help=t("cli.opt_output"))] = None,
    db: DbOpt = Path(const.DEFAULT_DB_FILENAME),
) -> None:
    try:
        with Store(db) as store:
            suite = store.load_suite(test_suite)
            if suite is None:
                _fail(
                    BenchmanError(
                        t(
                            "report.suite_not_found",
                            suite=test_suite,
                            available=", ".join(store.suite_names()) or "-",
                        )
                    )
                )
                return
            path = write_report(suite, output or Path(default_output_name(test_suite)))
    except BenchmanError as exc:
        _fail(exc)
        return
    typer.echo(t("report.written", path=path))


# --------------------------------------------------------------------------
# params
# --------------------------------------------------------------------------


@app.command(help=t("cli.params_help"))
def params(tool: Annotated[str, typer.Argument(help="tool")]) -> None:
    try:
        driver_cls = get_driver_class(tool)
    except KeyError:
        _fail(BenchmanError(t("driver.unknown", tool=tool, available=", ".join(available_tools()))))
        return
    spec = driver_cls.load_params_spec()
    typer.echo(t("params.header", tool=spec.meta.tool, count=len(spec.params)))
    groups = {group.id: group for group in spec.groups}
    header = (
        f"{t('params.column_name'):<16} {t('params.column_type'):<10} "
        f"{t('params.column_default'):<10} {t('params.column_range'):<14} "
        f"{t('params.column_sweepable'):<6} {t('params.column_label')}"
    )
    current_group = None
    for param in sorted(spec.params, key=lambda p: (p.group, p.name)):
        if param.group != current_group:
            current_group = param.group
            group = groups.get(current_group)
            label = group.label.get() if group else current_group
            typer.echo("")
            typer.secho(t("params.group", group=label), bold=True)
            typer.echo(header)
        typer.echo(
            f"{param.name:<16} {param.type:<10} "
            f"{str(param.default if param.default is not None else '-'):<10} "
            f"{param.range_text():<14} {'yes' if param.sweepable else '-':<6} "
            f"{param.label.get()}"
        )
    typer.echo("")
    typer.echo(t("params.raw_note"))


def main() -> None:
    """コンソールスクリプトのエントリポイント。"""
    app()


if __name__ == "__main__":
    main()
