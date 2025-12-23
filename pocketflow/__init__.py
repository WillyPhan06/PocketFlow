import asyncio, warnings, copy, time, contextvars
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict, Set, Tuple
from enum import Enum

class TraceEventType(Enum):
    NODE_START = "node_start"
    NODE_PREP = "node_prep"
    NODE_EXEC = "node_exec"
    NODE_POST = "node_post"
    NODE_END = "node_end"
    NODE_ERROR = "node_error"
    RETRY_ATTEMPT = "retry_attempt"
    RETRY_WAIT = "retry_wait"
    FALLBACK = "fallback"
    TRANSITION = "transition"
    FLOW_START = "flow_start"
    FLOW_END = "flow_end"

@dataclass
class TraceEvent:
    event_type: TraceEventType
    node_name: str
    timestamp: float = field(default_factory=time.time)
    data: Optional[Dict[str, Any]] = None

    def __repr__(self):
        data_str = f", data={self.data}" if self.data else ""
        return f"TraceEvent({self.event_type.value}, node={self.node_name}, t={self.timestamp:.4f}{data_str})"

@dataclass
class NodeError:
    """Captures error information from a failed node execution.

    This class represents an error state that can be routed through the flow
    instead of crashing. When a node's exec() fails after all retries, the
    default exec_fallback() returns a NodeError instead of raising the exception.

    The post() method can check for errors using is_error() and route accordingly:
        def post(self, shared, prep_res, exec_res):
            if self.is_error(exec_res):
                shared['last_error'] = exec_res
                return "error"  # Route to error handler
            return "success"
    """
    exception: Exception
    exception_type: str
    message: str
    node_name: str
    retry_count: int
    max_retries: int
    traceback_str: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

class FlowTracer:
    """Lightweight tracer for debugging flow execution.

    Usage:
        tracer = FlowTracer()
        flow.run(shared, tracer=tracer)

        # View execution trace
        tracer.print_summary()

        # Access raw events
        for event in tracer.events:
            print(event)
    """
    def __init__(self, capture_data: bool = False, max_data_size: int = 1000):
        """
        Args:
            capture_data: If True, captures prep/exec/post input/output data (may impact performance)
            max_data_size: Maximum string length for captured data (truncated if exceeded)
        """
        self.events: List[TraceEvent] = []
        self.capture_data = capture_data
        self.max_data_size = max_data_size
        self._start_time: Optional[float] = None

    def _truncate(self, data: Any) -> Any:
        """Truncate data representation if too large."""
        if data is None:
            return None
        s = repr(data)
        if len(s) > self.max_data_size:
            return s[:self.max_data_size] + "...[truncated]"
        return s

    def record(self, event_type: TraceEventType, node_name: str, data: Optional[Dict[str, Any]] = None):
        """Record a trace event."""
        if self._start_time is None:
            self._start_time = time.time()

        captured_data = None
        if data and self.capture_data:
            captured_data = {k: self._truncate(v) for k, v in data.items()}
        elif data:
            # Even without capture_data, record lightweight info like action names
            captured_data = {k: v for k, v in data.items() if k in ('action', 'retry', 'max_retries', 'wait_time', 'error', 'from_node', 'to_node', 'type', 'retry_count')}
            if not captured_data:
                captured_data = None

        self.events.append(TraceEvent(event_type, node_name, time.time(), captured_data))

    def get_execution_order(self) -> List[str]:
        """Get list of node names in execution order."""
        return [e.node_name for e in self.events if e.event_type == TraceEventType.NODE_START]

    def get_transitions(self) -> List[Dict[str, str]]:
        """Get list of all transitions with their actions."""
        return [
            {"from": e.data.get("from_node"), "to": e.data.get("to_node"), "action": e.data.get("action")}
            for e in self.events if e.event_type == TraceEventType.TRANSITION and e.data
        ]

    def get_retries(self) -> List[Dict[str, Any]]:
        """Get list of all retry events."""
        return [
            {"node": e.node_name, **e.data}
            for e in self.events if e.event_type == TraceEventType.RETRY_ATTEMPT and e.data
        ]

    def get_duration(self) -> float:
        """Get total execution duration in seconds."""
        if not self.events:
            return 0.0
        return self.events[-1].timestamp - self.events[0].timestamp

    def print_summary(self):
        """Print a human-readable summary of the execution trace."""
        if not self.events:
            print("No trace events recorded.")
            return

        print(f"\n{'='*60}")
        print("FLOW EXECUTION TRACE")
        print(f"{'='*60}")
        print(f"Total duration: {self.get_duration():.4f}s")
        print(f"Total events: {len(self.events)}")
        print(f"\nExecution order: {' -> '.join(self.get_execution_order())}")

        transitions = self.get_transitions()
        if transitions:
            print(f"\nTransitions:")
            for t in transitions:
                print(f"  {t['from']} --[{t['action']}]--> {t['to']}")

        retries = self.get_retries()
        if retries:
            print(f"\nRetries:")
            for r in retries:
                print(f"  {r['node']}: attempt {r.get('retry', '?')}/{r.get('max_retries', '?')}")

        print(f"\nDetailed timeline:")
        for event in self.events:
            rel_time = event.timestamp - self.events[0].timestamp
            data_str = f" | {event.data}" if event.data else ""
            print(f"  [{rel_time:>8.4f}s] {event.event_type.value:<15} {event.node_name}{data_str}")
        print(f"{'='*60}\n")

    def to_dict(self) -> Dict[str, Any]:
        """Export trace as a dictionary for serialization."""
        return {
            "duration": self.get_duration(),
            "execution_order": self.get_execution_order(),
            "transitions": self.get_transitions(),
            "retries": self.get_retries(),
            "events": [
                {
                    "type": e.event_type.value,
                    "node": e.node_name,
                    "timestamp": e.timestamp,
                    "data": e.data
                }
                for e in self.events
            ]
        }

    def clear(self):
        """Clear all recorded events."""
        self.events.clear()
        self._start_time = None


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
    successors: Dict[str, str]  # action -> target node name
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

    Usage:
        structure = FlowStructure(flow)
        structure.print_structure()  # Visual overview

        # Or get specific information
        nodes = structure.get_nodes()
        paths = structure.get_all_paths()
        issues = structure.validate()
    """

    def __init__(self, flow_or_node):
        """
        Args:
            flow_or_node: A Flow instance or any node that starts a chain
        """
        self._root = flow_or_node
        self._nodes: Dict[str, NodeInfo] = {}
        self._transitions: List[TransitionInfo] = []
        self._analyze()

    def _get_node_name(self, node) -> str:
        """Get display name for a node."""
        if hasattr(node, 'name') and node.name:
            return node.name
        return node.__class__.__name__

    def _get_node_type(self, node) -> str:
        """Get the type classification of a node."""
        return node.__class__.__name__

    def _is_flow(self, node) -> bool:
        """Check if node is a Flow type using isinstance."""
        # Import at runtime to avoid circular definition issues
        # Flow is defined later in the module
        return isinstance(node, Flow)

    def _is_async(self, node) -> bool:
        """Check if node is async using isinstance."""
        # AsyncNode is defined later in the module
        return isinstance(node, AsyncNode)

    def _is_batch(self, node) -> bool:
        """Check if node is a batch type using isinstance."""
        # BatchNode and BatchFlow are defined later in the module
        return isinstance(node, (BatchNode, BatchFlow))

    def _get_retry_config(self, node) -> Optional[Dict[str, Any]]:
        """Extract retry configuration from a node."""
        if hasattr(node, 'max_retries') and node.max_retries > 1:
            config = {'max_retries': node.max_retries}
            if hasattr(node, 'wait') and node.wait > 0:
                config['wait'] = node.wait
            if hasattr(node, 'exponential_backoff') and node.exponential_backoff:
                config['exponential_backoff'] = True
            if hasattr(node, 'max_wait') and node.max_wait is not None:
                config['max_wait'] = node.max_wait
            return config
        return None

    def _analyze(self):
        """Analyze the flow structure starting from root."""
        visited: Set[int] = set()
        self._traverse(self._root, visited)

    def _traverse(self, node, visited: Set[int]):
        """Recursively traverse and catalog all nodes."""
        if node is None:
            return

        node_id = id(node)
        if node_id in visited:
            return
        visited.add(node_id)

        node_name = self._get_node_name(node)

        # Handle name collisions by appending instance id
        original_name = node_name
        counter = 1
        while node_name in self._nodes and id(self._nodes[node_name]) != node_id:
            node_name = f"{original_name}_{counter}"
            counter += 1

        # Build successor mapping
        successors: Dict[str, str] = {}
        for action, successor in node.successors.items():
            successor_name = self._get_node_name(successor)
            successors[action] = successor_name
            self._transitions.append(TransitionInfo(node_name, successor_name, action))

        # If this is a Flow, add transition to its start_node (internal entry)
        if self._is_flow(node) and hasattr(node, 'start_node') and node.start_node:
            start_node_name = self._get_node_name(node.start_node)
            successors['_start'] = start_node_name
            self._transitions.append(TransitionInfo(node_name, start_node_name, '_start'))

        # Create node info
        self._nodes[node_name] = NodeInfo(
            name=node_name,
            node_type=self._get_node_type(node),
            successors=successors,
            retry_config=self._get_retry_config(node),
            is_flow=self._is_flow(node),
            is_async=self._is_async(node),
            is_batch=self._is_batch(node)
        )

        # If this is a Flow, also traverse its internal structure
        if self._is_flow(node) and hasattr(node, 'start_node') and node.start_node:
            self._traverse(node.start_node, visited)

        # Traverse successors
        for successor in node.successors.values():
            self._traverse(successor, visited)

    def get_nodes(self) -> Dict[str, NodeInfo]:
        """Get all nodes in the flow."""
        return dict(self._nodes)

    def get_node(self, name: str) -> Optional[NodeInfo]:
        """Get information about a specific node."""
        return self._nodes.get(name)

    def get_transitions(self) -> List[TransitionInfo]:
        """Get all transitions between nodes."""
        return list(self._transitions)

    def get_actions(self) -> Set[str]:
        """Get all unique action names used in the flow."""
        return {t.action for t in self._transitions}

    def get_entry_points(self) -> List[str]:
        """Get nodes that could be entry points (not targeted by any transition)."""
        targeted = {t.to_node for t in self._transitions}
        return [name for name in self._nodes if name not in targeted]

    def get_exit_points(self) -> List[str]:
        """Get nodes that are exit points (no outgoing transitions)."""
        return [name for name, info in self._nodes.items() if not info.successors]

    def get_successors(self, node_name: str) -> Dict[str, str]:
        """Get the successors of a node (action -> target node name)."""
        node = self._nodes.get(node_name)
        return dict(node.successors) if node else {}

    def get_predecessors(self, node_name: str) -> List[Tuple[str, str]]:
        """Get nodes that transition to this node (list of (from_node, action) tuples)."""
        return [(t.from_node, t.action) for t in self._transitions if t.to_node == node_name]

    def _find_paths(self, start: str, end: Optional[str], visited: Set[str],
                    path: List[str], actions: List[str], max_depth: int) -> List[PathInfo]:
        """Recursively find paths through the flow."""
        if max_depth <= 0:
            return []

        paths = []
        node = self._nodes.get(start)

        if not node:
            return []

        # Check if we've completed a path
        if end is None and not node.successors:
            # Path to any exit point
            paths.append(PathInfo(list(path), list(actions), has_loop=False))
        elif end is not None and start == end and len(path) > 1:
            # Path to specific endpoint (and we've moved at least once)
            paths.append(PathInfo(list(path), list(actions), has_loop=False))

        # Continue exploring
        for action, next_node in node.successors.items():
            has_loop = next_node in visited
            if has_loop:
                # Record the loop path but don't continue traversing
                loop_path = path + [next_node]
                loop_actions = actions + [action]
                paths.append(PathInfo(loop_path, loop_actions, has_loop=True))
            else:
                # Continue traversal
                new_visited = visited | {next_node}
                new_path = path + [next_node]
                new_actions = actions + [action]
                paths.extend(self._find_paths(next_node, end, new_visited,
                                              new_path, new_actions, max_depth - 1))

        return paths

    def get_all_paths(self, from_node: Optional[str] = None, to_node: Optional[str] = None,
                      max_depth: int = 50) -> List[PathInfo]:
        """Get all possible paths through the flow.

        Args:
            from_node: Starting node (default: first entry point)
            to_node: Target node (default: any exit point)
            max_depth: Maximum path length to prevent infinite loops

        Returns:
            List of PathInfo objects describing each possible path
        """
        if from_node is None:
            entry_points = self.get_entry_points()
            if not entry_points:
                return []
            from_node = entry_points[0]

        if from_node not in self._nodes:
            return []

        return self._find_paths(from_node, to_node, {from_node}, [from_node], [], max_depth)

    def has_loops(self) -> bool:
        """Check if the flow contains any loops/cycles."""
        paths = self.get_all_paths()
        return any(p.has_loop for p in paths)

    def get_loops(self) -> List[PathInfo]:
        """Get all paths that contain loops."""
        return [p for p in self.get_all_paths() if p.has_loop]

    def validate(self) -> List[Dict[str, Any]]:
        """Validate the flow structure and return any issues found.

        Returns:
            List of issue dictionaries with 'type', 'message', and 'severity' keys
        """
        issues = []

        # Check for missing start node in flows
        root_name = self._get_node_name(self._root)
        if self._is_flow(self._root):
            if not hasattr(self._root, 'start_node') or self._root.start_node is None:
                issues.append({
                    'type': 'missing_start',
                    'message': f"Flow '{root_name}' has no start node defined",
                    'severity': 'error'
                })

        # Check for unreachable nodes (not the root and not targeted by transitions)
        entry_points = set(self.get_entry_points())
        if len(entry_points) > 1:
            for node_name in entry_points:
                if node_name != root_name:
                    # Check if it's a start_node of a flow
                    is_flow_start = False
                    for name, info in self._nodes.items():
                        if info.is_flow and node_name in info.successors.values():
                            is_flow_start = True
                            break
                    if not is_flow_start:
                        issues.append({
                            'type': 'unreachable_node',
                            'message': f"Node '{node_name}' may be unreachable (no incoming transitions)",
                            'severity': 'warning'
                        })

        # Check for potential infinite loops (loops with no exit conditions)
        # First, find all unique cycles by identifying nodes that loop back
        checked_cycles: Set[frozenset] = set()
        loops = self.get_loops()

        for loop in loops:
            if len(loop.nodes) < 2:
                continue

            # Build the actual cycle: from the loopback target to the node that loops back
            loopback_target = loop.nodes[-1]  # Node being looped back to
            loopback_target_idx = loop.nodes.index(loopback_target)  # First occurrence
            cycle_nodes = set(loop.nodes[loopback_target_idx:-1])  # Nodes in the cycle

            # Skip if we already checked this cycle
            cycle_key = frozenset(cycle_nodes)
            if cycle_key in checked_cycles:
                continue
            checked_cycles.add(cycle_key)

            # Check if there's ANY exit from the cycle
            # An exit is a transition from a cycle node to a node outside the cycle
            has_exit = False
            for node_name in cycle_nodes:
                node = self._nodes.get(node_name)
                if node:
                    for action, next_node in node.successors.items():
                        if next_node not in cycle_nodes:
                            has_exit = True
                            break
                if has_exit:
                    break

            if not has_exit:
                cycle_repr = ' -> '.join(loop.nodes[loopback_target_idx:])
                issues.append({
                    'type': 'potential_infinite_loop',
                    'message': f"Loop detected ({cycle_repr}) with no apparent exit",
                    'severity': 'warning'
                })

        return issues

    def print_structure(self):
        """Print a human-readable overview of the flow structure."""
        print(f"\n{'='*60}")
        print("FLOW STRUCTURE")
        print(f"{'='*60}")

        root_name = self._get_node_name(self._root)
        print(f"Root: {root_name}")
        print(f"Total nodes: {len(self._nodes)}")
        print(f"Total transitions: {len(self._transitions)}")

        # Entry and exit points
        entry_points = self.get_entry_points()
        exit_points = self.get_exit_points()
        print(f"\nEntry points: {', '.join(entry_points) if entry_points else '(none)'}")
        print(f"Exit points: {', '.join(exit_points) if exit_points else '(none)'}")

        # Actions used
        actions = self.get_actions()
        if actions:
            print(f"Actions used: {', '.join(sorted(actions))}")

        # Nodes
        print(f"\n{'─'*40}")
        print("NODES")
        print(f"{'─'*40}")
        for name, info in self._nodes.items():
            type_flags = []
            if info.is_flow:
                type_flags.append("Flow")
            if info.is_async:
                type_flags.append("Async")
            if info.is_batch:
                type_flags.append("Batch")

            flags_str = f" [{', '.join(type_flags)}]" if type_flags else ""
            print(f"\n  {name} ({info.node_type}){flags_str}")

            if info.retry_config:
                retry_str = f"max_retries={info.retry_config['max_retries']}"
                if 'wait' in info.retry_config:
                    retry_str += f", wait={info.retry_config['wait']}s"
                if info.retry_config.get('exponential_backoff'):
                    retry_str += ", exponential"
                print(f"    Retry: {retry_str}")

            if info.successors:
                print(f"    Transitions:")
                for action, target in info.successors.items():
                    print(f"      --[{action}]--> {target}")
            else:
                print(f"    (exit point)")

        # Paths
        print(f"\n{'─'*40}")
        print("POSSIBLE PATHS")
        print(f"{'─'*40}")
        paths = self.get_all_paths()
        if paths:
            for i, path in enumerate(paths[:10], 1):  # Limit to first 10
                path_str = ' -> '.join(path.nodes)
                loop_marker = " (LOOP)" if path.has_loop else ""
                print(f"  {i}. {path_str}{loop_marker}")
            if len(paths) > 10:
                print(f"  ... and {len(paths) - 10} more paths")
        else:
            print("  (no complete paths found)")

        # Validation
        issues = self.validate()
        if issues:
            print(f"\n{'─'*40}")
            print("ISSUES FOUND")
            print(f"{'─'*40}")
            for issue in issues:
                severity = issue['severity'].upper()
                print(f"  [{severity}] {issue['message']}")

        print(f"\n{'='*60}\n")

    def to_dict(self) -> Dict[str, Any]:
        """Export structure as a dictionary for serialization."""
        return {
            'root': self._get_node_name(self._root),
            'nodes': {
                name: {
                    'name': info.name,
                    'type': info.node_type,
                    'successors': info.successors,
                    'retry_config': info.retry_config,
                    'is_flow': info.is_flow,
                    'is_async': info.is_async,
                    'is_batch': info.is_batch
                }
                for name, info in self._nodes.items()
            },
            'transitions': [
                {'from': t.from_node, 'to': t.to_node, 'action': t.action}
                for t in self._transitions
            ],
            'entry_points': self.get_entry_points(),
            'exit_points': self.get_exit_points(),
            'actions': list(self.get_actions()),
            'has_loops': self.has_loops(),
            'issues': self.validate()
        }

    def to_mermaid(self) -> str:
        """Generate a Mermaid diagram of the flow structure."""
        lines = ["graph LR"]

        # Add nodes
        for name, info in self._nodes.items():
            # Escape special characters in node names
            safe_name = name.replace(" ", "_").replace("-", "_")
            display_name = name.replace("'", "")

            if info.is_flow:
                lines.append(f"    {safe_name}[[\"{display_name}\"]]")
            elif info.is_batch:
                lines.append(f"    {safe_name}[/\"{display_name}\"/]")
            else:
                lines.append(f"    {safe_name}[\"{display_name}\"]")

        # Add transitions
        for t in self._transitions:
            from_safe = t.from_node.replace(" ", "_").replace("-", "_")
            to_safe = t.to_node.replace(" ", "_").replace("-", "_")
            if t.action == "default" or t.action == "_start":
                # Default and _start (internal flow entry) are shown without labels
                lines.append(f"    {from_safe} --> {to_safe}")
            else:
                lines.append(f"    {from_safe} -->|{t.action}| {to_safe}")

        return "\n".join(lines)

    def compare_with_trace(self, tracer: 'FlowTracer') -> Dict[str, Any]:
        """Compare the static structure with actual execution trace.

        This helps identify:
        - Which paths were actually taken vs available paths
        - Nodes that were never executed
        - Unexpected transitions

        Args:
            tracer: A FlowTracer with recorded execution data

        Returns:
            Comparison results dictionary
        """
        executed_nodes = set(tracer.get_execution_order())
        all_nodes = set(self._nodes.keys())

        executed_transitions = {
            (t['from'], t['to'], t['action'])
            for t in tracer.get_transitions()
        }
        all_transitions = {
            (t.from_node, t.to_node, t.action)
            for t in self._transitions
        }

        return {
            'executed_nodes': list(executed_nodes),
            'unexecuted_nodes': list(all_nodes - executed_nodes),
            'executed_transitions': [
                {'from': t[0], 'to': t[1], 'action': t[2]}
                for t in executed_transitions
            ],
            'unused_transitions': [
                {'from': t[0], 'to': t[1], 'action': t[2]}
                for t in all_transitions - executed_transitions
            ],
            'coverage': {
                'nodes': len(executed_nodes) / len(all_nodes) if all_nodes else 1.0,
                'transitions': len(executed_transitions) / len(all_transitions) if all_transitions else 1.0
            }
        }


# Context variable for async-safe tracer access (isolated per async task/coroutine)
_current_tracer: contextvars.ContextVar[Optional[FlowTracer]] = contextvars.ContextVar('_current_tracer', default=None)

def _get_current_tracer() -> Optional[FlowTracer]:
    """Get the current tracer from context (async-safe)."""
    return _current_tracer.get()

def _set_current_tracer(tracer: Optional[FlowTracer]) -> contextvars.Token:
    """Set the current tracer in context, returns token for reset."""
    return _current_tracer.set(tracer)

def _reset_current_tracer(token: contextvars.Token):
    """Reset tracer to previous value using token."""
    _current_tracer.reset(token)

def _get_node_name(node) -> str:
    """Get a readable name for a node, with caching."""
    # Check for cached name first
    cached = getattr(node, '_cached_trace_name', None)
    if cached is not None:
        return cached
    # Compute and cache
    if hasattr(node, 'name') and node.name:
        name = node.name
    else:
        name = node.__class__.__name__
    # Cache on instance (avoid repeated computation in large flows)
    try:
        node._cached_trace_name = name
    except AttributeError:
        pass  # Some objects don't allow attribute setting
    return name

class BaseNode:
    def __init__(self): self.params,self.successors,self.name,self._cached_trace_name={},{},None,None
    def set_params(self,params): self.params=params
    @staticmethod
    def is_error(result): return isinstance(result, NodeError)
    def next(self,node,action="default"):
        if action in self.successors: warnings.warn(f"Overwriting successor for action '{action}'")
        self.successors[action]=node; return node
    def prep(self,shared): pass
    def exec(self,prep_res): pass
    def post(self,shared,prep_res,exec_res): pass
    def _exec(self,prep_res,tracer=None): return self.exec(prep_res)
    def _run(self,shared,tracer=None):
        # Note: NODE_START/NODE_END are recorded at orchestration level (Flow._orch) for defensive tracing
        # This ensures tracing works even if _run is overridden by custom nodes
        # Returns (action, exec_result) tuple for orchestrator to handle error routing
        tracer = tracer or _get_current_tracer()
        node_name = _get_node_name(self) if tracer else None
        p = self.prep(shared)
        if tracer: tracer.record(TraceEventType.NODE_PREP, node_name, {"prep_result": p} if tracer.capture_data else None)
        e = self._exec(p, tracer)
        if tracer: tracer.record(TraceEventType.NODE_EXEC, node_name, {"exec_result": e} if tracer.capture_data else None)
        action = self.post(shared, p, e)
        if tracer: tracer.record(TraceEventType.NODE_POST, node_name, {"action": action} if action else None)
        return (action, e)  # Return tuple: (action from post, exec result)
    def run(self,shared,tracer=None):
        if self.successors: warnings.warn("Node won't run successors. Use Flow.")
        # For standalone node.run(), we need to record NODE_START/END here since no orchestrator
        tracer_to_use = tracer or _get_current_tracer()
        node_name = _get_node_name(self) if tracer_to_use else None
        if tracer_to_use: tracer_to_use.record(TraceEventType.NODE_START, node_name)
        action, _ = self._run(shared, tracer)  # Unpack tuple, only return action for public API
        if tracer_to_use: tracer_to_use.record(TraceEventType.NODE_END, node_name)
        return action
    def __rshift__(self,other): return self.next(other)
    def __sub__(self,action):
        if isinstance(action,str): return _ConditionalTransition(self,action)
        raise TypeError("Action must be a string")

class _ConditionalTransition:
    def __init__(self,src,action): self.src,self.action=src,action
    def __rshift__(self,tgt): return self.src.next(tgt,self.action)

class Node(BaseNode):
    def __init__(self,max_retries=1,wait=0,exponential_backoff=False,max_wait=None): super().__init__(); self.max_retries,self.wait,self.exponential_backoff,self.max_wait=max_retries,wait,exponential_backoff,max_wait
    def exec_fallback(self,prep_res,exc):
        import traceback as tb
        return NodeError(exception=exc,exception_type=type(exc).__name__,message=str(exc),node_name=_get_node_name(self),retry_count=self.cur_retry+1,max_retries=self.max_retries,traceback_str=tb.format_exc())
    def _get_wait_time(self,retry_count):
        if self.wait<=0: return 0
        w=self.wait*(2**retry_count) if self.exponential_backoff else self.wait
        return min(w,self.max_wait) if self.max_wait is not None else w
    def _exec(self,prep_res,tracer=None):
        tracer = tracer or _get_current_tracer()
        node_name = _get_node_name(self)
        self.cur_retry=0
        for i in range(self.max_retries):
            self.cur_retry=i
            if tracer and self.max_retries > 1:
                tracer.record(TraceEventType.RETRY_ATTEMPT, node_name, {"retry": i+1, "max_retries": self.max_retries})
            try: return self.exec(prep_res)
            except Exception as e:
                if i==self.max_retries-1:
                    if tracer: tracer.record(TraceEventType.FALLBACK, node_name, {"error": str(e)})
                    result = self.exec_fallback(prep_res,e)
                    if isinstance(result, NodeError) and tracer:
                        tracer.record(TraceEventType.NODE_ERROR, node_name, {"error": result.message, "type": result.exception_type, "retry_count": result.retry_count})
                    return result
                w=self._get_wait_time(i)
                if tracer: tracer.record(TraceEventType.RETRY_WAIT, node_name, {"wait_time": w, "error": str(e)})
                if w>0: time.sleep(w)

class BatchNode(Node):
    def _exec(self,items,tracer=None): return [super(BatchNode,self)._exec(copy.deepcopy(i),tracer) for i in (items or [])]

class Flow(BaseNode):
    def __init__(self,start=None): super().__init__(); self.start_node=start
    def start(self,start): self.start_node=start; return start
    def get_next_node(self,curr,action):
        nxt=curr.successors.get(action or "default")
        if not nxt and curr.successors: warnings.warn(f"Flow ends: '{action}' not found in {list(curr.successors)}")
        return nxt
    def _orch(self,shared,params=None,tracer=None):
        tracer = tracer or _get_current_tracer()
        curr,p,last_action = copy.copy(self.start_node),(params or {**self.params}),None
        # Cache flow name once for all transitions
        flow_name = _get_node_name(self) if tracer else None
        while curr:
            curr.set_params(p)
            # Cache current node name before _run (defensive: works even if _run is overridden)
            curr_name = _get_node_name(curr) if tracer else None
            # Defensive tracing: record NODE_START at orchestration level in case _run is overridden
            if tracer: tracer.record(TraceEventType.NODE_START, curr_name)
            # _run returns (action, exec_result) tuple; handle backward compat if custom _run returns just action
            run_result = curr._run(shared, tracer)
            if isinstance(run_result, tuple):
                last_action, exec_result = run_result
            else:
                last_action, exec_result = run_result, None  # Backward compat: custom _run returning just action
            # Defensive tracing: record NODE_END at orchestration level
            if tracer: tracer.record(TraceEventType.NODE_END, curr_name)
            # Auto error routing: if exec result was NodeError and "error" successor exists, route there
            if isinstance(exec_result, NodeError) and "error" in curr.successors:
                shared["_error"] = exec_result  # Store error in shared for error handler
                last_action = "error"
            nxt = self.get_next_node(curr, last_action)
            if tracer and nxt:
                tracer.record(TraceEventType.TRANSITION, flow_name, {
                    "from_node": curr_name,
                    "to_node": _get_node_name(nxt),
                    "action": last_action or "default"
                })
            curr = copy.copy(nxt)
        return last_action
    def _run(self,shared,tracer=None): p=self.prep(shared); o=self._orch(shared,tracer=tracer); return (self.post(shared,p,o), None)  # Return tuple (action, None) for consistency
    def run(self,shared,tracer=None):
        token = _set_current_tracer(tracer) if tracer else None
        flow_name = _get_node_name(self)
        if tracer: tracer.record(TraceEventType.FLOW_START, flow_name)
        try:
            if self.successors: warnings.warn("Node won't run successors. Use Flow.")
            action, _ = self._run(shared, tracer)  # Unpack tuple, only return action for public API
            if tracer: tracer.record(TraceEventType.FLOW_END, flow_name)
            return action
        finally:
            if token: _reset_current_tracer(token)
    def post(self,shared,prep_res,exec_res): return exec_res

class BatchFlow(Flow):
    @staticmethod
    def _deep_merge(target,source,original):
        """
        Merge changes from source into target, using original as the baseline reference.

        WHY THIS EXISTS:
        In batch processing, each iteration runs with an isolated copy of shared state to prevent
        cross-iteration pollution. However, we still need to accumulate results back into the
        original shared dict. This function intelligently merges only the CHANGES made by each
        iteration, not the entire state.

        WHY COMPARE AGAINST ORIGINAL:
        We compare against 'original' (the state before ANY iteration ran) to detect what each
        iteration actually changed. Without this comparison, we couldn't distinguish between:
        - A value that was already there (shouldn't overwrite accumulated results)
        - A value that this iteration explicitly set (should be merged)

        Args:
            target: The shared dict accumulating results from all iterations
            source: The shared_copy from one iteration (contains that iteration's changes)
            original: Snapshot of shared state before any iteration ran (the baseline)
        """
        for k,v in source.items():
            orig_v=original.get(k) if original else None

            if k not in target:
                # WHY: Key didn't exist before - this iteration created it, so add it.
                # This handles new results that iterations produce.
                target[k]=copy.deepcopy(v)

            elif isinstance(target[k],dict) and isinstance(v,dict):
                # WHY RECURSE FOR DICTS: Dicts often hold results keyed by iteration ID
                # (e.g., shared['results'][batch_id] = value). We need to merge nested keys
                # from each iteration rather than replacing the entire dict, otherwise
                # iteration 2's results would overwrite iteration 1's results.
                orig_dict=orig_v if isinstance(orig_v,dict) else None
                BatchFlow._deep_merge(target[k],v,orig_dict)

            elif isinstance(target[k],list) and isinstance(v,list):
                # WHY EXTEND FOR LISTS: Lists are commonly used to accumulate results
                # (e.g., shared['results'].append(value)). Each iteration starts with the
                # original list and appends its own items. We only want to add the NEW items
                # that this iteration appended, not duplicate items from the original.
                #
                # HOW: If original had [a,b] and source now has [a,b,c,d], we only add [c,d]
                # to target. This preserves items added by previous iterations while adding
                # this iteration's contributions.
                orig_list=orig_v if isinstance(orig_v,list) else []
                new_items=v[len(orig_list):]
                target[k].extend(copy.deepcopy(new_items))

            elif v!=orig_v:
                # WHY CHECK v!=orig_v: Only update if this iteration actually changed the value.
                # If v equals orig_v, it means this iteration didn't modify it - the value is
                # just carried over from the original state. We skip it to avoid overwriting
                # changes made by previous iterations.
                #
                # NOTE ON TYPE CHANGES: If a value changes type between iterations (e.g., from
                # int to dict), this branch handles it by replacement. The dict/list special
                # cases above only apply when BOTH target and source have the same type.
                # Type changes are treated as simple value updates.
                target[k]=copy.deepcopy(v)
    def _run(self,shared,tracer=None):
        pr=self.prep(shared) or []
        original_shared=copy.deepcopy(shared)  # Snapshot original state
        for bp in pr:
            shared_copy=copy.deepcopy(original_shared)  # Each iteration starts from original
            params_copy=copy.deepcopy({**self.params,**bp})
            self._orch(shared_copy,params_copy,tracer)
            # Merge only changes from shared_copy into shared
            BatchFlow._deep_merge(shared,shared_copy,original_shared)
        return (self.post(shared,pr,None), None)  # Return tuple for consistency

class AsyncNode(Node):
    async def prep_async(self,shared): pass
    async def exec_async(self,prep_res): pass
    async def exec_fallback_async(self,prep_res,exc):
        import traceback as tb
        return NodeError(exception=exc,exception_type=type(exc).__name__,message=str(exc),node_name=_get_node_name(self),retry_count=self.cur_retry+1,max_retries=self.max_retries,traceback_str=tb.format_exc())
    async def post_async(self,shared,prep_res,exec_res): pass
    async def _exec(self,prep_res,tracer=None):
        tracer = tracer or _get_current_tracer()
        node_name = _get_node_name(self) if tracer else None
        self.cur_retry=0
        for i in range(self.max_retries):
            self.cur_retry=i
            if tracer and self.max_retries > 1:
                tracer.record(TraceEventType.RETRY_ATTEMPT, node_name, {"retry": i+1, "max_retries": self.max_retries})
            try: return await self.exec_async(prep_res)
            except Exception as e:
                if i==self.max_retries-1:
                    if tracer: tracer.record(TraceEventType.FALLBACK, node_name, {"error": str(e)})
                    result = await self.exec_fallback_async(prep_res,e)
                    if isinstance(result, NodeError) and tracer:
                        tracer.record(TraceEventType.NODE_ERROR, node_name, {"error": result.message, "type": result.exception_type, "retry_count": result.retry_count})
                    return result
                w=self._get_wait_time(i)
                if tracer: tracer.record(TraceEventType.RETRY_WAIT, node_name, {"wait_time": w, "error": str(e)})
                if w>0: await asyncio.sleep(w)
    async def run_async(self,shared,tracer=None):
        if self.successors: warnings.warn("Node won't run successors. Use AsyncFlow.")
        # For standalone node.run_async(), we need to record NODE_START/END here since no orchestrator
        tracer_to_use = tracer or _get_current_tracer()
        node_name = _get_node_name(self) if tracer_to_use else None
        if tracer_to_use: tracer_to_use.record(TraceEventType.NODE_START, node_name)
        action, _ = await self._run_async(shared,tracer)  # Unpack tuple, only return action for public API
        if tracer_to_use: tracer_to_use.record(TraceEventType.NODE_END, node_name)
        return action
    async def _run_async(self,shared,tracer=None):
        # Note: NODE_START/NODE_END are recorded at orchestration level (AsyncFlow._orch_async) for defensive tracing
        # Returns (action, exec_result) tuple for orchestrator to handle error routing
        tracer = tracer or _get_current_tracer()
        node_name = _get_node_name(self) if tracer else None
        p=await self.prep_async(shared)
        if tracer: tracer.record(TraceEventType.NODE_PREP, node_name, {"prep_result": p} if tracer.capture_data else None)
        e=await self._exec(p,tracer)
        if tracer: tracer.record(TraceEventType.NODE_EXEC, node_name, {"exec_result": e} if tracer.capture_data else None)
        action=await self.post_async(shared,p,e)
        if tracer: tracer.record(TraceEventType.NODE_POST, node_name, {"action": action} if action else None)
        return (action, e)  # Return tuple: (action from post, exec result)
    def _run(self,shared,tracer=None): raise RuntimeError("Use run_async.")

class AsyncBatchNode(AsyncNode,BatchNode):
    async def _exec(self,items,tracer=None): return [await super(AsyncBatchNode,self)._exec(copy.deepcopy(i),tracer) for i in (items or [])]

class AsyncParallelBatchNode(AsyncNode,BatchNode):
    def __init__(self,max_retries=1,wait=0,exponential_backoff=False,max_wait=None,concurrency_limit=None):
        super().__init__(max_retries,wait,exponential_backoff,max_wait)
        if concurrency_limit is not None and concurrency_limit<1: raise ValueError("concurrency_limit must be at least 1")
        self.concurrency_limit=concurrency_limit; self._semaphore=asyncio.Semaphore(concurrency_limit) if concurrency_limit else None
        self._concurrent_task_count=0; self._concurrent_task_lock=asyncio.Lock()
    def get_concurrent_task_count(self): return self._concurrent_task_count
    async def _exec(self,items,tracer=None):
        async def tracked_exec(i,use_semaphore=False):
            if use_semaphore: await self._semaphore.acquire()
            async with self._concurrent_task_lock: self._concurrent_task_count+=1
            try: return await super(AsyncParallelBatchNode,self)._exec(copy.deepcopy(i),tracer)
            finally:
                async with self._concurrent_task_lock: self._concurrent_task_count-=1
                if use_semaphore: self._semaphore.release()
        if self._semaphore: return await asyncio.gather(*(tracked_exec(i,True) for i in (items or [])))
        return await asyncio.gather(*(tracked_exec(i) for i in (items or [])))

class AsyncFlow(Flow,AsyncNode):
    def __init__(self,start=None,concurrency_limit=None):
        super().__init__(start)
        if concurrency_limit is not None and concurrency_limit<1: raise ValueError("concurrency_limit must be at least 1")
        self.concurrency_limit=concurrency_limit; self._semaphore=asyncio.Semaphore(concurrency_limit) if concurrency_limit else None
    async def _orch_async(self,shared,params=None,tracer=None):
        tracer = tracer or _get_current_tracer()
        curr,p,last_action = copy.copy(self.start_node),(params or {**self.params}),None
        # Cache flow name once for all transitions
        flow_name = _get_node_name(self) if tracer else None
        while curr:
            curr.set_params(p)
            # Cache current node name before _run (defensive: works even if _run_async is overridden)
            curr_name = _get_node_name(curr) if tracer else None
            # Defensive tracing: record NODE_START at orchestration level
            if tracer: tracer.record(TraceEventType.NODE_START, curr_name)
            # _run/_run_async returns (action, exec_result) tuple; handle backward compat if custom _run returns just action
            if isinstance(curr,AsyncNode):
                run_result = await curr._run_async(shared,tracer)
            else:
                run_result = curr._run(shared,tracer)
            if isinstance(run_result, tuple):
                last_action, exec_result = run_result
            else:
                last_action, exec_result = run_result, None  # Backward compat: custom _run returning just action
            # Defensive tracing: record NODE_END at orchestration level
            if tracer: tracer.record(TraceEventType.NODE_END, curr_name)
            # Auto error routing: if exec result was NodeError and "error" successor exists, route there
            if isinstance(exec_result, NodeError) and "error" in curr.successors:
                shared["_error"] = exec_result  # Store error in shared for error handler
                last_action = "error"
            nxt = self.get_next_node(curr, last_action)
            if tracer and nxt:
                tracer.record(TraceEventType.TRANSITION, flow_name, {
                    "from_node": curr_name,
                    "to_node": _get_node_name(nxt),
                    "action": last_action or "default"
                })
            curr = copy.copy(nxt)
        return last_action
    async def _run_async(self,shared,tracer=None): p=await self.prep_async(shared); o=await self._orch_async(shared,tracer=tracer); return (await self.post_async(shared,p,o), None)  # Return tuple for consistency
    async def run_async(self,shared,tracer=None):
        token = _set_current_tracer(tracer) if tracer else None
        flow_name = _get_node_name(self)
        if tracer: tracer.record(TraceEventType.FLOW_START, flow_name)
        try:
            if self.successors: warnings.warn("Node won't run successors. Use Flow.")
            action, _ = await self._run_async(shared, tracer)  # Unpack tuple, only return action for public API
            if tracer: tracer.record(TraceEventType.FLOW_END, flow_name)
            return action
        finally:
            if token: _reset_current_tracer(token)
    async def post_async(self,shared,prep_res,exec_res): return exec_res

class AsyncBatchFlow(AsyncFlow,BatchFlow):
    async def _run_async(self,shared,tracer=None):
        pr=await self.prep_async(shared) or []
        original_shared=copy.deepcopy(shared)  # Snapshot original state
        for bp in pr:
            shared_copy=copy.deepcopy(original_shared)  # Each iteration starts from original
            params_copy=copy.deepcopy({**self.params,**bp})
            await self._orch_async(shared_copy,params_copy,tracer)
            # Merge only changes from shared_copy into shared
            BatchFlow._deep_merge(shared,shared_copy,original_shared)
        return (await self.post_async(shared,pr,None), None)  # Return tuple for consistency

class AsyncParallelBatchFlow(AsyncFlow,BatchFlow):
    def __init__(self,start=None,concurrency_limit=None):
        super().__init__(start,concurrency_limit)
        self._concurrent_task_count=0; self._concurrent_task_lock=asyncio.Lock()
    def get_concurrent_task_count(self): return self._concurrent_task_count
    async def _run_async(self,shared,tracer=None):
        pr=await self.prep_async(shared) or []
        original_shared=copy.deepcopy(shared)  # Snapshot original state
        async def tracked_orch(bp,idx,use_semaphore=False):
            if use_semaphore: await self._semaphore.acquire()
            async with self._concurrent_task_lock: self._concurrent_task_count+=1
            try:
                shared_copy=copy.deepcopy(original_shared)  # Each task starts from original
                params_copy=copy.deepcopy({**self.params,**bp})
                await self._orch_async(shared_copy,params_copy,tracer)
                return (idx,shared_copy)  # Return index and modified shared copy
            finally:
                async with self._concurrent_task_lock: self._concurrent_task_count-=1
                if use_semaphore: self._semaphore.release()
        if self._semaphore: results=await asyncio.gather(*(tracked_orch(bp,i,True) for i,bp in enumerate(pr)))
        else: results=await asyncio.gather(*(tracked_orch(bp,i) for i,bp in enumerate(pr)))
        # Sort by index to ensure deterministic merge order
        for idx,shared_copy in sorted(results,key=lambda x:x[0]):
            BatchFlow._deep_merge(shared,shared_copy,original_shared)
        return (await self.post_async(shared,pr,None), None)  # Return tuple for consistency