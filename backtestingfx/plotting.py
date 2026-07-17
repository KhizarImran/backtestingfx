import html
import math
from pathlib import Path
import webbrowser

import pandas as pd


def render_report(
    data,
    stats,
    *,
    strategy_name,
    filename,
    open_browser,
    commission,
    spread,
    contract_size,
    quote_to_account,
):
    try:
        import plotly.graph_objects as go
        import plotly.io as pio
        from plotly.subplots import make_subplots
    except ModuleNotFoundError as error:
        raise ModuleNotFoundError(
            'Plotting requires Plotly. Install it with: pip install "backtestingfx[report]"'
        ) from error

    if data.empty:
        raise ValueError("Cannot plot a backtest with no bars")

    columns = {str(column).lower(): column for column in data.columns}
    if isinstance(data.index, pd.DatetimeIndex):
        timestamps = pd.to_datetime(data.index, utc=True)
    else:
        timestamps = pd.to_datetime(data[columns["timestamp"]], utc=True)

    equity = list(stats.equity_curve)
    if len(equity) == len(data) + 1:
        equity = equity[1:]
    if len(equity) != len(data):
        raise ValueError("Equity curve does not match the number of bars")

    equity_series = pd.Series(equity, dtype=float)
    peaks = pd.Series([stats.initial_cash, *equity], dtype=float).cummax().iloc[1:]
    peaks.index = equity_series.index
    drawdown = ((equity_series / peaks) - 1.0).fillna(0.0) * 100.0
    trades = list(stats.trades)

    chart = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.045,
        row_heights=[0.58, 0.24, 0.18],
    )
    chart.add_trace(
        go.Candlestick(
            x=timestamps,
            open=data[columns["open"]],
            high=data[columns["high"]],
            low=data[columns["low"]],
            close=data[columns["close"]],
            name="Price",
            increasing_line_color="#45d483",
            decreasing_line_color="#ff6b57",
        ),
        row=1,
        col=1,
    )

    entry_lines_x = []
    entry_lines_y = []
    for trade in trades:
        entry_lines_x.extend(
            [
                pd.to_datetime(trade.entry_timestamp, unit="s", utc=True),
                pd.to_datetime(trade.exit_timestamp, unit="s", utc=True),
                None,
            ]
        )
        entry_lines_y.extend([trade.entry_price, trade.exit_price, None])
    if trades:
        chart.add_trace(
            go.Scatter(
                x=entry_lines_x,
                y=entry_lines_y,
                mode="lines",
                line={"color": "rgba(190, 190, 190, 0.28)", "width": 1},
                hoverinfo="skip",
                showlegend=False,
            ),
            row=1,
            col=1,
        )

    for is_long, label, color, symbol in (
        (True, "Long entry", "#45d483", "triangle-up"),
        (False, "Short entry", "#ff6b57", "triangle-down"),
    ):
        matching = [trade for trade in trades if trade.is_long == is_long]
        if matching:
            chart.add_trace(
                go.Scatter(
                    x=[pd.to_datetime(t.entry_timestamp, unit="s", utc=True) for t in matching],
                    y=[t.entry_price for t in matching],
                    mode="markers",
                    name=label,
                    marker={"color": color, "size": 11, "symbol": symbol},
                    customdata=[[t.lot_size, t.pnl] for t in matching],
                    hovertemplate=(
                        f"{label}<br>%{{x}}<br>Price %{{y:.5f}}"
                        "<br>Lots %{customdata[0]:.2f}<br>Net PnL %{customdata[1]:.2f}<extra></extra>"
                    ),
                ),
                row=1,
                col=1,
            )

    if trades:
        chart.add_trace(
            go.Scatter(
                x=[pd.to_datetime(t.exit_timestamp, unit="s", utc=True) for t in trades],
                y=[t.exit_price for t in trades],
                mode="markers",
                name="Exit",
                marker={
                    "color": ["#45d483" if t.pnl >= 0 else "#ff6b57" for t in trades],
                    "line": {"color": "#080808", "width": 1},
                    "size": 9,
                    "symbol": "circle",
                },
                customdata=[[t.pnl] for t in trades],
                hovertemplate=(
                    "Exit<br>%{x}<br>Price %{y:.5f}"
                    "<br>Net PnL %{customdata[0]:.2f}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )

    chart.add_trace(
        go.Scatter(
            x=timestamps,
            y=equity,
            mode="lines",
            name="Equity",
            line={"color": "#f1c75b", "width": 2},
            hovertemplate="%{x}<br>Equity %{y:,.2f}<extra></extra>",
        ),
        row=2,
        col=1,
    )
    chart.add_hline(
        y=stats.initial_cash,
        line={"color": "rgba(255,255,255,0.22)", "dash": "dot"},
        row=2,
        col=1,
    )
    chart.add_trace(
        go.Scatter(
            x=timestamps,
            y=drawdown,
            mode="lines",
            name="Drawdown",
            line={"color": "#ff6b57", "width": 1.5},
            fill="tozeroy",
            fillcolor="rgba(255, 107, 87, 0.20)",
            hovertemplate="%{x}<br>Drawdown %{y:.2f}%<extra></extra>",
        ),
        row=3,
        col=1,
    )
    chart.update_layout(
        height=920,
        margin={"l": 60, "r": 25, "t": 35, "b": 35},
        paper_bgcolor="#111111",
        plot_bgcolor="#111111",
        font={"color": "#d0d0d0", "family": "IBM Plex Mono, ui-monospace, monospace"},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.03, "x": 0},
        xaxis_rangeslider_visible=False,
    )
    chart.update_xaxes(gridcolor="rgba(255,255,255,0.06)", showspikes=True)
    chart.update_yaxes(gridcolor="rgba(255,255,255,0.06)", zeroline=False)
    chart.update_yaxes(title_text="Price", row=1, col=1)
    chart.update_yaxes(title_text="Equity", row=2, col=1)
    chart.update_yaxes(title_text="Drawdown %", row=3, col=1)

    analytics = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=("Trade PnL distribution", "Cumulative realized PnL"),
        horizontal_spacing=0.12,
    )
    pnls = [trade.pnl for trade in trades]
    if pnls:
        analytics.add_trace(
            go.Histogram(x=pnls, marker_color="#9da3ad", name="Trade PnL"),
            row=1,
            col=1,
        )
        cumulative = pd.Series(pnls).cumsum()
        analytics.add_trace(
            go.Scatter(
                x=list(range(1, len(pnls) + 1)),
                y=cumulative,
                mode="lines+markers",
                line={"color": "#45d483", "width": 2},
                marker={"size": 5},
                name="Cumulative PnL",
            ),
            row=1,
            col=2,
        )
    else:
        analytics.add_annotation(
            text="No completed trades",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
        )
    analytics.update_layout(
        height=390,
        margin={"l": 55, "r": 25, "t": 55, "b": 45},
        paper_bgcolor="#111111",
        plot_bgcolor="#111111",
        font={"color": "#d0d0d0", "family": "IBM Plex Mono, ui-monospace, monospace"},
        showlegend=False,
    )
    analytics.update_xaxes(gridcolor="rgba(255,255,255,0.06)")
    analytics.update_yaxes(gridcolor="rgba(255,255,255,0.06)", zeroline=False)

    def number(value, suffix="", money=False):
        if math.isinf(value):
            return "&infin;"
        prefix = "$" if money else ""
        return f"{prefix}{value:,.2f}{suffix}"

    metric_values = (
        ("Total return", number(stats.total_return_pct, "%")),
        ("Final equity", number(stats.final_cash, money=True)),
        ("Max drawdown", number(stats.max_drawdown_pct, "%")),
        ("Sharpe", number(stats.sharpe_ratio)),
        ("Trades", str(stats.num_trades)),
        ("Win rate", number(stats.win_rate_pct, "%")),
        ("Profit factor", number(stats.profit_factor)),
        ("Average trade", number(stats.avg_pnl, money=True)),
    )
    metrics_html = "".join(
        f'<div class="metric"><span>{label}</span><strong>{value}</strong></div>'
        for label, value in metric_values
    )

    rows = []
    for trade in trades:
        entry_time = pd.to_datetime(trade.entry_timestamp, unit="s", utc=True)
        exit_time = pd.to_datetime(trade.exit_timestamp, unit="s", utc=True)
        duration = exit_time - entry_time
        result_class = "positive" if trade.pnl >= 0 else "negative"
        rows.append(
            "<tr>"
            f"<td>{'LONG' if trade.is_long else 'SHORT'}</td>"
            f"<td>{html.escape(entry_time.strftime('%Y-%m-%d %H:%M'))}</td>"
            f"<td>{html.escape(exit_time.strftime('%Y-%m-%d %H:%M'))}</td>"
            f"<td>{trade.entry_price:.5f}</td>"
            f"<td>{trade.exit_price:.5f}</td>"
            f"<td>{trade.lot_size:.2f}</td>"
            f'<td class="{result_class}">{trade.pnl:,.2f}</td>'
            f"<td>{html.escape(str(duration))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="8" class="empty">No completed trades</td></tr>')

    config = {"displaylogo": False, "responsive": True, "scrollZoom": True}
    chart_html = pio.to_html(
        chart,
        full_html=False,
        include_plotlyjs=True,
        config=config,
    )
    analytics_html = pio.to_html(
        analytics,
        full_html=False,
        include_plotlyjs=False,
        config=config,
    )
    start = timestamps[0].strftime("%Y-%m-%d %H:%M UTC")
    end = timestamps[-1].strftime("%Y-%m-%d %H:%M UTC")
    safe_strategy_name = html.escape(strategy_name)

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_strategy_name} | backtestingfx report</title>
  <style>
    :root {{ color-scheme: dark; --bg: #080808; --panel: #111111; --line: #2b2b2b; --ink: #f1f1f1; --muted: #929292; --accent: #d8d8d8; --green: #45d483; --red: #ff6b57; --gold: #f1c75b; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    body::before {{ content: ""; position: fixed; inset: 0; pointer-events: none; background-image: linear-gradient(rgba(255,255,255,.018) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.018) 1px, transparent 1px); background-size: 42px 42px; mask-image: linear-gradient(to bottom, black, transparent 65%); }}
    main {{ width: min(1500px, calc(100% - 40px)); margin: 0 auto; padding: 38px 0 70px; position: relative; }}
    header {{ display: flex; justify-content: space-between; gap: 30px; align-items: end; padding: 8px 0 28px; border-bottom: 1px solid var(--line); }}
    .eyebrow {{ color: var(--accent); font: 700 12px/1.4 ui-monospace, monospace; letter-spacing: .18em; text-transform: uppercase; }}
    h1 {{ margin: 8px 0 14px; font-size: clamp(34px, 4.5vw, 64px); line-height: 1.08; letter-spacing: -.045em; overflow-wrap: anywhere; }}
    .period {{ color: var(--muted); font: 13px/1.6 ui-monospace, monospace; }}
    .status {{ border: 1px solid #444; color: var(--accent); background: rgba(255,255,255,.04); border-radius: 999px; padding: 9px 14px; font: 700 11px ui-monospace, monospace; letter-spacing: .12em; white-space: nowrap; }}
    .metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; margin: 28px 0; background: var(--line); border: 1px solid var(--line); }}
    .metric {{ background: var(--panel); padding: 18px 20px; min-height: 98px; display: flex; flex-direction: column; justify-content: space-between; }}
    .metric span, .section-label {{ color: var(--muted); font: 700 10px ui-monospace, monospace; letter-spacing: .14em; text-transform: uppercase; }}
    .metric strong {{ font: 600 clamp(21px, 2vw, 31px) ui-monospace, monospace; letter-spacing: -.04em; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); margin-top: 18px; overflow: hidden; }}
    .panel-head {{ display: flex; justify-content: space-between; align-items: center; padding: 18px 22px; border-bottom: 1px solid var(--line); }}
    .panel-head h2 {{ margin: 0; font-size: 17px; letter-spacing: -.02em; }}
    .assumptions {{ display: grid; grid-template-columns: repeat(4, 1fr); border-top: 1px solid var(--line); }}
    .assumption {{ padding: 15px 20px; border-right: 1px solid var(--line); }}
    .assumption:last-child {{ border-right: 0; }}
    .assumption span {{ display: block; color: var(--muted); font: 10px ui-monospace, monospace; text-transform: uppercase; letter-spacing: .1em; margin-bottom: 5px; }}
    .assumption strong {{ font: 14px ui-monospace, monospace; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font: 12px ui-monospace, monospace; }}
    th {{ color: var(--muted); text-align: left; font-size: 10px; letter-spacing: .09em; text-transform: uppercase; }}
    th, td {{ padding: 13px 16px; border-bottom: 1px solid var(--line); white-space: nowrap; }}
    tbody tr:hover {{ background: rgba(255,255,255,.025); }}
    .positive {{ color: var(--green); }} .negative {{ color: var(--red); }} .empty {{ color: var(--muted); text-align: center; padding: 30px; }}
    footer {{ color: var(--muted); display: flex; justify-content: space-between; margin-top: 28px; font: 10px ui-monospace, monospace; letter-spacing: .08em; text-transform: uppercase; }}
    @media (max-width: 900px) {{ .metrics {{ grid-template-columns: repeat(2, 1fr); }} .assumptions {{ grid-template-columns: repeat(2, 1fr); }} header {{ align-items: start; flex-direction: column; }} }}
    @media (max-width: 560px) {{ main {{ width: min(100% - 20px, 1500px); padding-top: 20px; }} .metrics {{ grid-template-columns: 1fr; }} .assumptions {{ grid-template-columns: 1fr; }} .metric {{ min-height: 82px; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <div><div class="eyebrow">backtestingfx / strategy report</div><h1>{safe_strategy_name}</h1><div class="period">{start} &rarr; {end} &nbsp; / &nbsp; {len(data):,} bars</div></div>
      <div class="status">RUN COMPLETE</div>
    </header>
    <section class="metrics">{metrics_html}</section>
    <section class="panel">
      <div class="panel-head"><h2>Market replay</h2><span class="section-label">Price / Equity / Drawdown</span></div>
      {chart_html}
      <div class="assumptions">
        <div class="assumption"><span>Commission / lot / side</span><strong>{commission:,.4f}</strong></div>
        <div class="assumption"><span>Spread offset</span><strong>{spread:,.5f}</strong></div>
        <div class="assumption"><span>Contract size</span><strong>{contract_size:,.0f}</strong></div>
        <div class="assumption"><span>Quote conversion</span><strong>{quote_to_account:,.5f}</strong></div>
      </div>
    </section>
    <section class="panel"><div class="panel-head"><h2>Trade diagnostics</h2><span class="section-label">Distribution / Sequence</span></div>{analytics_html}</section>
    <section class="panel">
      <div class="panel-head"><h2>Trade ledger</h2><span class="section-label">{len(trades)} completed</span></div>
      <div class="table-wrap"><table><thead><tr><th>Side</th><th>Entry time</th><th>Exit time</th><th>Entry</th><th>Exit</th><th>Lots</th><th>Net PnL</th><th>Duration</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
    </section>
    <footer><span>Generated by backtestingfx</span><span>Research output, not financial advice</span></footer>
  </main>
</body>
</html>"""

    output = Path(filename).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    if open_browser:
        webbrowser.open(output.as_uri())
    return str(output)
