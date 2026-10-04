"""Harness Farm Account Manager.

Watches account usage, balance, spend, pacing, and renewals; adjusts connections automatically
and generates alerts for human tasks or threshold events.
"""

from farm.manager.alerts import ack_alert, create_alert, list_open_alerts
from farm.manager.balance import (
    compute_ledger_remaining,
    record_balance_snapshot,
    sync_all_balances,
    sync_connection_balance,
)
from farm.manager.budgets import (
    PolicyDecision,
    calculate_month_forecast,
    check_budget_thresholds,
    check_paid_call,
    get_month_to_date_spend,
    record_policy_block,
)
from farm.manager.pacing import (
    PacingStatus,
    compute_daily_pacing_budget,
    get_connection_pacing,
    is_over_pace,
    pace_rank_penalty,
)
from farm.manager.renewals import (
    check_renewal_reminders,
    compute_next_renewal_date,
    request_human_task,
)
from farm.manager.scheduler import (
    ManagerScheduler,
    reactivate_due_connections,
    run_maintenance_tick,
)
from farm.manager.telegram import (
    clear_telegram_state,
    is_telegram_configured,
    send_telegram_alert,
)

__all__ = [
    "ManagerScheduler",
    "PacingStatus",
    "PolicyDecision",
    "ack_alert",
    "calculate_month_forecast",
    "check_budget_thresholds",
    "check_paid_call",
    "check_renewal_reminders",
    "clear_telegram_state",
    "compute_daily_pacing_budget",
    "compute_ledger_remaining",
    "compute_next_renewal_date",
    "create_alert",
    "get_connection_pacing",
    "get_month_to_date_spend",
    "is_over_pace",
    "is_telegram_configured",
    "list_open_alerts",
    "pace_rank_penalty",
    "reactivate_due_connections",
    "record_balance_snapshot",
    "record_policy_block",
    "request_human_task",
    "run_maintenance_tick",
    "send_telegram_alert",
    "sync_all_balances",
    "sync_connection_balance",
]
