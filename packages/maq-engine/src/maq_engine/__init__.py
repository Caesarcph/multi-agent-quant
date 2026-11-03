"""Engine for multi-agent-quant: event log, order manager, execution pipeline."""

from maq_engine.events import (
    DayStarted,
    IssueKind,
    KillSwitchExecuted,
    OrderRequested,
    OrderSendFailed,
    OrderUpdated,
    PositionCloseRequested,
    ReconciliationIssue,
)
from maq_engine.orders import OrderManager, OrderRecord, ReconcileReport
from maq_engine.pipeline import ExecutionPipeline, PipelineResult, average_true_range
from maq_engine.store import (
    DEFAULT_EVENT_DB,
    GENESIS_HASH,
    EventStore,
    StoredEvent,
    VerifyReport,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_EVENT_DB",
    "GENESIS_HASH",
    "DayStarted",
    "EventStore",
    "ExecutionPipeline",
    "IssueKind",
    "KillSwitchExecuted",
    "OrderManager",
    "OrderRecord",
    "OrderRequested",
    "OrderSendFailed",
    "OrderUpdated",
    "PipelineResult",
    "PositionCloseRequested",
    "ReconcileReport",
    "ReconciliationIssue",
    "StoredEvent",
    "VerifyReport",
    "average_true_range",
]
