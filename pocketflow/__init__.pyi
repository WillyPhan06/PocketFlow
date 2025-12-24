import asyncio
import contextvars
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Union, TypeVar, Generic, Set, Tuple, Literal, Callable

# Type variables for better type relationships
_PrepResult = TypeVar('_PrepResult')
_ExecResult = TypeVar('_ExecResult')
_PostResult = TypeVar('_PostResult')

# More specific parameter types
ParamValue = Union[str, int, float, bool, None, List[Any], Dict[str, Any]]
SharedData = Dict[str, Any]
Params = Dict[str, ParamValue]

# Type alias for sort_by parameter in timing methods
SortByPhase = Literal['total', 'prep', 'exec', 'post']

# --- Tracing Types ---

class TraceEventType(Enum):
    NODE_START: str
    NODE_PREP: str
    NODE_EXEC: str
    NODE_POST: str
    NODE_END: str
    NODE_ERROR: str
    RETRY_ATTEMPT: str
    RETRY_WAIT: str
    FALLBACK: str
    TRANSITION: str
    FLOW_START: str
    FLOW_END: str

@dataclass
class TraceEvent:
    event_type: TraceEventType
    node_name: str
    timestamp: float
    data: Optional[Dict[str, Any]]

    def __repr__(self) -> str: ...

@dataclass
class NodeTiming:
    """Timing information for a single node execution.

    This dataclass captures the duration of each phase (prep, exec, post) of node
    execution, helping developers identify performance bottlenecks.

    Attributes:
        node_name: The name of the node.
        prep_time: Time in seconds for the prep() phase. None if not recorded.
        exec_time: Time in seconds for the exec() phase. None if not recorded.
        post_time: Time in seconds for the post() phase. None if not recorded.
        total_time: Total time from NODE_START to NODE_END in seconds. None if incomplete.
        start_timestamp: Absolute timestamp when node started.
        end_timestamp: Absolute timestamp when node ended. None if not yet ended.
    """
    node_name: str
    prep_time: Optional[float] = None
    exec_time: Optional[float] = None
    post_time: Optional[float] = None
    total_time: Optional[float] = None
    start_timestamp: Optional[float] = None
    end_timestamp: Optional[float] = None

    def __repr__(self) -> str: ...

@dataclass
class NodeError:
    """Captures error information from a failed node execution.

    This class represents an error state that can be routed through the flow
    instead of crashing. When a node's exec() fails after all retries, the
    default exec_fallback() returns a NodeError instead of raising the exception.
    """
    exception: Exception
    exception_type: str
    message: str
    node_name: str
    retry_count: int
    max_retries: int
    traceback_str: Optional[str]
    timestamp: float

class FlowTracer:
    """Lightweight tracer for debugging flow execution."""
    events: List[TraceEvent]
    capture_data: bool
    max_data_size: int
    _start_time: Optional[float]

    def __init__(self, capture_data: bool = False, max_data_size: int = 1000) -> None: ...
    def _truncate(self, data: Any) -> Any: ...
    def record(self, event_type: TraceEventType, node_name: str, data: Optional[Dict[str, Any]] = None) -> None: ...
    def get_execution_order(self) -> List[str]: ...
    def get_transitions(self) -> List[Dict[str, str]]: ...
    def get_retries(self) -> List[Dict[str, Any]]: ...
    def get_duration(self) -> float: ...
    def get_node_timings(self) -> List[NodeTiming]:
        """Get timing information for all executed nodes.

        Calculates prep, exec, and post phase durations for each node by analyzing
        the recorded trace events. Returns a list of NodeTiming objects with
        detailed timing breakdowns.

        Returns:
            List of NodeTiming objects, one per node execution in order of execution.
        """
        ...
    def _get_sort_key(self, sort_by: SortByPhase) -> Callable[[NodeTiming], float]:
        """Get the sorting key function for a given sort_by parameter.

        Args:
            sort_by: One of 'total', 'prep', 'exec', or 'post'.

        Returns:
            A function that extracts the timing value for sorting.

        Raises:
            ValueError: If sort_by is not a valid option.
        """
        ...
    def get_slowest_nodes(self, n: Optional[int] = None, sort_by: SortByPhase = 'total') -> List[NodeTiming]:
        """Get the slowest nodes sorted by the specified timing phase.

        Useful for identifying multiple performance bottlenecks in your flow.
        Returns nodes sorted from slowest to fastest.

        Args:
            n: Maximum number of nodes to return. None returns all nodes sorted.
            sort_by: Which timing to sort by. One of:
                - 'total': Total execution time (default)
                - 'prep': Time spent in prep() phase
                - 'exec': Time spent in exec() phase
                - 'post': Time spent in post() phase

        Returns:
            List of NodeTiming objects sorted from slowest to fastest.

        Raises:
            ValueError: If sort_by is not a valid option.
        """
        ...
    def get_slowest_node(self, sort_by: SortByPhase = 'total') -> Optional[NodeTiming]:
        """Get the timing info for the slowest node.

        Useful for quickly identifying the main performance bottleneck in your flow.

        Args:
            sort_by: Which timing to use for finding the slowest node. One of:
                - 'total': Total execution time (default)
                - 'prep': Time spent in prep() phase
                - 'exec': Time spent in exec() phase
                - 'post': Time spent in post() phase

        Returns:
            NodeTiming for the slowest node, or None if no nodes were traced.

        Raises:
            ValueError: If sort_by is not a valid option.
        """
        ...
    def print_timing_table(self) -> None:
        """Print a formatted table showing timing for each node.

        Displays node name, prep time, exec time, post time, and total time
        in a tabular format. Also highlights the slowest node to help identify
        performance bottlenecks.
        """
        ...
    def print_summary(self) -> None: ...
    def to_dict(self) -> Dict[str, Any]: ...
    def clear(self) -> None: ...

# --- Flow Structure Types ---

@dataclass
class NodeInfo:
    """Information about a node in the flow structure.

    Attributes:
        name: The display name of the node, either from node.name or the class name.
        node_type: The class name of the node (e.g., "Node", "AsyncNode", "Flow").
        successors: Mapping of action names to target node names (e.g., {"default": "NextNode"}).
        retry_config: Retry configuration if max_retries > 1, containing keys like
            'max_retries', 'wait', 'exponential_backoff', 'max_wait'. None if no retry.
        is_flow: True if the node is a Flow or subclass (Flow, AsyncFlow, BatchFlow, etc.).
        is_async: True if the node is an async type (AsyncNode, AsyncFlow, etc.).
        is_batch: True if the node is a batch type (BatchNode, BatchFlow, etc.).
    """
    name: str
    node_type: str
    successors: Dict[str, str]
    retry_config: Optional[Dict[str, Any]] = None
    is_flow: bool = False
    is_async: bool = False
    is_batch: bool = False

@dataclass
class TransitionInfo:
    """Information about a transition between nodes.

    Attributes:
        from_node: The name of the source node where the transition originates.
        to_node: The name of the target node where the transition leads.
        action: The action string that triggers this transition (e.g., "default", "yes", "error").
    """
    from_node: str
    to_node: str
    action: str

@dataclass
class PathInfo:
    """Information about a path through the flow.

    Attributes:
        nodes: Ordered list of node names representing the path (e.g., ["Start", "Process", "End"]).
        actions: List of action strings taken between nodes. Length is len(nodes) - 1.
        has_loop: True if this path contains a cycle (revisits a previously visited node).
    """
    nodes: List[str]
    actions: List[str]
    has_loop: bool = False

class FlowStructure:
    """Static analyzer for flow structure - see how flows will execute before running.

    FlowStructure provides pre-execution visibility into your flow:
    - What nodes exist and how they connect
    - All possible paths through the flow
    - Available transitions and actions
    - Potential issues (unreachable nodes, missing transitions, loops)

    While FlowTracer shows what happened during execution, FlowStructure shows
    what CAN happen before you run anything.
    """
    _root: BaseNode[Any, Any, Any]
    _nodes: Dict[str, NodeInfo]
    _transitions: List[TransitionInfo]

    def __init__(self, flow_or_node: BaseNode[Any, Any, Any]) -> None: ...
    def _get_node_name(self, node: BaseNode[Any, Any, Any]) -> str: ...
    def _get_node_type(self, node: BaseNode[Any, Any, Any]) -> str: ...
    def _is_flow(self, node: BaseNode[Any, Any, Any]) -> bool: ...
    def _is_async(self, node: BaseNode[Any, Any, Any]) -> bool: ...
    def _is_batch(self, node: BaseNode[Any, Any, Any]) -> bool: ...
    def _get_retry_config(self, node: BaseNode[Any, Any, Any]) -> Optional[Dict[str, Any]]: ...
    def _analyze(self) -> None: ...
    def _traverse(self, node: BaseNode[Any, Any, Any], visited: Set[int]) -> None: ...
    def get_nodes(self) -> Dict[str, NodeInfo]: ...
    def get_node(self, name: str) -> Optional[NodeInfo]: ...
    def get_transitions(self) -> List[TransitionInfo]: ...
    def get_actions(self) -> Set[str]: ...
    def get_entry_points(self) -> List[str]: ...
    def get_exit_points(self) -> List[str]: ...
    def get_successors(self, node_name: str) -> Dict[str, str]: ...
    def get_predecessors(self, node_name: str) -> List[Tuple[str, str]]: ...
    def _find_paths(
        self,
        start: str,
        end: Optional[str],
        visited: Set[str],
        path: List[str],
        actions: List[str],
        max_depth: int
    ) -> List[PathInfo]: ...
    def get_all_paths(
        self,
        from_node: Optional[str] = None,
        to_node: Optional[str] = None,
        max_depth: int = 50
    ) -> List[PathInfo]: ...
    def has_loops(self) -> bool: ...
    def get_loops(self) -> List[PathInfo]: ...
    def validate(self) -> List[Dict[str, Any]]: ...
    def print_structure(self) -> None: ...
    def to_dict(self) -> Dict[str, Any]: ...
    def to_mermaid(self) -> str: ...
    def compare_with_trace(self, tracer: FlowTracer) -> Dict[str, Any]: ...

# --- Context Variable and Helper Functions ---

_current_tracer: contextvars.ContextVar[Optional[FlowTracer]]

def _get_current_tracer() -> Optional[FlowTracer]: ...
def _set_current_tracer(tracer: Optional[FlowTracer]) -> contextvars.Token[Optional[FlowTracer]]: ...
def _reset_current_tracer(token: contextvars.Token[Optional[FlowTracer]]) -> None: ...
def _get_node_name(node: Any) -> str: ...

# --- Node and Flow Types ---

class BaseNode(Generic[_PrepResult, _ExecResult, _PostResult]):
    params: Params
    successors: Dict[str, BaseNode[Any, Any, Any]]
    name: Optional[str]
    _cached_trace_name: Optional[str]

    def __init__(self) -> None: ...
    def set_params(self, params: Params) -> None: ...
    @staticmethod
    def is_error(result: Any) -> bool: ...
    def next(self, node: BaseNode[Any, Any, Any], action: str = "default") -> BaseNode[Any, Any, Any]: ...
    def prep(self, shared: SharedData) -> _PrepResult: ...
    def exec(self, prep_res: _PrepResult) -> _ExecResult: ...
    def post(self, shared: SharedData, prep_res: _PrepResult, exec_res: _ExecResult) -> _PostResult: ...
    def _exec(self, prep_res: _PrepResult, tracer: Optional[FlowTracer] = None) -> _ExecResult: ...
    def _run(self, shared: SharedData, tracer: Optional[FlowTracer] = None) -> Tuple[_PostResult, Optional[Union[_ExecResult, NodeError]]]: ...
    def run(self, shared: SharedData, tracer: Optional[FlowTracer] = None) -> _PostResult: ...
    def __rshift__(self, other: BaseNode[Any, Any, Any]) -> BaseNode[Any, Any, Any]: ...
    def __sub__(self, action: str) -> _ConditionalTransition: ...

class _ConditionalTransition:
    src: BaseNode[Any, Any, Any]
    action: str

    def __init__(self, src: BaseNode[Any, Any, Any], action: str) -> None: ...
    def __rshift__(self, tgt: BaseNode[Any, Any, Any]) -> BaseNode[Any, Any, Any]: ...

class Node(BaseNode[_PrepResult, _ExecResult, _PostResult]):
    max_retries: int
    wait: Union[int, float]
    exponential_backoff: bool
    max_wait: Optional[Union[int, float]]
    cur_retry: int

    def __init__(self, max_retries: int = 1, wait: Union[int, float] = 0, exponential_backoff: bool = False, max_wait: Optional[Union[int, float]] = None) -> None: ...
    def exec_fallback(self, prep_res: _PrepResult, exc: Exception) -> Union[_ExecResult, NodeError]: ...
    def _get_wait_time(self, retry_count: int) -> Union[int, float]: ...
    def _exec(self, prep_res: _PrepResult, tracer: Optional[FlowTracer] = None) -> Union[_ExecResult, NodeError]: ...

class BatchNode(Node[Optional[List[_PrepResult]], List[_ExecResult], _PostResult]):
    def _exec(self, items: Optional[List[_PrepResult]], tracer: Optional[FlowTracer] = None) -> List[_ExecResult]: ...

class Flow(BaseNode[_PrepResult, Any, _PostResult]):
    start_node: Optional[BaseNode[Any, Any, Any]]

    def __init__(self, start: Optional[BaseNode[Any, Any, Any]] = None) -> None: ...
    def start(self, start: BaseNode[Any, Any, Any]) -> BaseNode[Any, Any, Any]: ...
    def get_next_node(
        self, curr: BaseNode[Any, Any, Any], action: Optional[str]
    ) -> Optional[BaseNode[Any, Any, Any]]: ...
    def _orch(
        self, shared: SharedData, params: Optional[Params] = None, tracer: Optional[FlowTracer] = None
    ) -> Any: ...
    def _run(self, shared: SharedData, tracer: Optional[FlowTracer] = None) -> Tuple[_PostResult, None]: ...
    def run(self, shared: SharedData, tracer: Optional[FlowTracer] = None) -> _PostResult: ...
    def post(self, shared: SharedData, prep_res: _PrepResult, exec_res: Any) -> _PostResult: ...

class BatchFlow(Flow[Optional[List[Params]], Any, _PostResult]):
    def _run(self, shared: SharedData, tracer: Optional[FlowTracer] = None) -> Tuple[_PostResult, None]: ...

class AsyncNode(Node[_PrepResult, _ExecResult, _PostResult]):
    async def prep_async(self, shared: SharedData) -> _PrepResult: ...
    async def exec_async(self, prep_res: _PrepResult) -> _ExecResult: ...
    async def exec_fallback_async(self, prep_res: _PrepResult, exc: Exception) -> Union[_ExecResult, NodeError]: ...
    async def post_async(
        self, shared: SharedData, prep_res: _PrepResult, exec_res: Union[_ExecResult, NodeError]
    ) -> _PostResult: ...
    async def _exec(self, prep_res: _PrepResult, tracer: Optional[FlowTracer] = None) -> Union[_ExecResult, NodeError]: ...
    async def run_async(self, shared: SharedData, tracer: Optional[FlowTracer] = None) -> _PostResult: ...
    async def _run_async(self, shared: SharedData, tracer: Optional[FlowTracer] = None) -> Tuple[_PostResult, Optional[Union[_ExecResult, NodeError]]]: ...
    def _run(self, shared: SharedData, tracer: Optional[FlowTracer] = None) -> _PostResult: ...

class AsyncBatchNode(AsyncNode[Optional[List[_PrepResult]], List[_ExecResult], _PostResult], BatchNode[Optional[List[_PrepResult]], List[_ExecResult], _PostResult]):
    async def _exec(self, items: Optional[List[_PrepResult]], tracer: Optional[FlowTracer] = None) -> List[_ExecResult]: ...

class AsyncParallelBatchNode(AsyncNode[Optional[List[_PrepResult]], List[_ExecResult], _PostResult], BatchNode[Optional[List[_PrepResult]], List[_ExecResult], _PostResult]):
    concurrency_limit: Optional[int]
    _semaphore: Optional[asyncio.Semaphore]
    _concurrent_task_count: int
    _concurrent_task_lock: asyncio.Lock

    def __init__(
        self,
        max_retries: int = 1,
        wait: Union[int, float] = 0,
        exponential_backoff: bool = False,
        max_wait: Optional[Union[int, float]] = None,
        concurrency_limit: Optional[int] = None
    ) -> None: ...
    def get_concurrent_task_count(self) -> int: ...
    async def _exec(self, items: Optional[List[_PrepResult]], tracer: Optional[FlowTracer] = None) -> List[_ExecResult]: ...

class AsyncFlow(Flow[_PrepResult, Any, _PostResult], AsyncNode[_PrepResult, Any, _PostResult]):
    concurrency_limit: Optional[int]
    _semaphore: Optional[asyncio.Semaphore]

    def __init__(
        self,
        start: Optional[BaseNode[Any, Any, Any]] = None,
        concurrency_limit: Optional[int] = None
    ) -> None: ...
    async def _orch_async(
        self, shared: SharedData, params: Optional[Params] = None, tracer: Optional[FlowTracer] = None
    ) -> Any: ...
    async def _run_async(self, shared: SharedData, tracer: Optional[FlowTracer] = None) -> Tuple[_PostResult, None]: ...
    async def run_async(self, shared: SharedData, tracer: Optional[FlowTracer] = None) -> _PostResult: ...
    async def post_async(
        self, shared: SharedData, prep_res: _PrepResult, exec_res: Any
    ) -> _PostResult: ...

class AsyncBatchFlow(AsyncFlow[Optional[List[Params]], Any, _PostResult], BatchFlow[Optional[List[Params]], Any, _PostResult]):
    async def _run_async(self, shared: SharedData, tracer: Optional[FlowTracer] = None) -> Tuple[_PostResult, None]: ...

class AsyncParallelBatchFlow(AsyncFlow[Optional[List[Params]], Any, _PostResult], BatchFlow[Optional[List[Params]], Any, _PostResult]):
    _concurrent_task_count: int
    _concurrent_task_lock: asyncio.Lock

    def __init__(
        self,
        start: Optional[BaseNode[Any, Any, Any]] = None,
        concurrency_limit: Optional[int] = None
    ) -> None: ...
    def get_concurrent_task_count(self) -> int: ...
    async def _run_async(self, shared: SharedData, tracer: Optional[FlowTracer] = None) -> Tuple[_PostResult, None]: ...
