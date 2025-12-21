import asyncio, warnings, copy, time, contextvars
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict
from enum import Enum

class TraceEventType(Enum):
    NODE_START = "node_start"
    NODE_PREP = "node_prep"
    NODE_EXEC = "node_exec"
    NODE_POST = "node_post"
    NODE_END = "node_end"
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
            captured_data = {k: v for k, v in data.items() if k in ('action', 'retry', 'max_retries', 'wait_time', 'error', 'from_node', 'to_node')}
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
        tracer = tracer or _get_current_tracer()
        node_name = _get_node_name(self) if tracer else None
        p = self.prep(shared)
        if tracer: tracer.record(TraceEventType.NODE_PREP, node_name, {"prep_result": p} if tracer.capture_data else None)
        e = self._exec(p, tracer)
        if tracer: tracer.record(TraceEventType.NODE_EXEC, node_name, {"exec_result": e} if tracer.capture_data else None)
        result = self.post(shared, p, e)
        if tracer: tracer.record(TraceEventType.NODE_POST, node_name, {"action": result} if result else None)
        return result
    def run(self,shared,tracer=None):
        if self.successors: warnings.warn("Node won't run successors. Use Flow.")
        # For standalone node.run(), we need to record NODE_START/END here since no orchestrator
        tracer_to_use = tracer or _get_current_tracer()
        node_name = _get_node_name(self) if tracer_to_use else None
        if tracer_to_use: tracer_to_use.record(TraceEventType.NODE_START, node_name)
        result = self._run(shared, tracer)
        if tracer_to_use: tracer_to_use.record(TraceEventType.NODE_END, node_name)
        return result
    def __rshift__(self,other): return self.next(other)
    def __sub__(self,action):
        if isinstance(action,str): return _ConditionalTransition(self,action)
        raise TypeError("Action must be a string")

class _ConditionalTransition:
    def __init__(self,src,action): self.src,self.action=src,action
    def __rshift__(self,tgt): return self.src.next(tgt,self.action)

class Node(BaseNode):
    def __init__(self,max_retries=1,wait=0,exponential_backoff=False,max_wait=None): super().__init__(); self.max_retries,self.wait,self.exponential_backoff,self.max_wait=max_retries,wait,exponential_backoff,max_wait
    def exec_fallback(self,prep_res,exc): raise exc
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
                    return self.exec_fallback(prep_res,e)
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
            last_action = curr._run(shared, tracer)
            # Defensive tracing: record NODE_END at orchestration level
            if tracer: tracer.record(TraceEventType.NODE_END, curr_name)
            nxt = self.get_next_node(curr, last_action)
            if tracer and nxt:
                tracer.record(TraceEventType.TRANSITION, flow_name, {
                    "from_node": curr_name,
                    "to_node": _get_node_name(nxt),
                    "action": last_action or "default"
                })
            curr = copy.copy(nxt)
        return last_action
    def _run(self,shared,tracer=None): p=self.prep(shared); o=self._orch(shared,tracer=tracer); return self.post(shared,p,o)
    def run(self,shared,tracer=None):
        token = _set_current_tracer(tracer) if tracer else None
        flow_name = _get_node_name(self)
        if tracer: tracer.record(TraceEventType.FLOW_START, flow_name)
        try:
            if self.successors: warnings.warn("Node won't run successors. Use Flow.")
            result = self._run(shared, tracer)
            if tracer: tracer.record(TraceEventType.FLOW_END, flow_name)
            return result
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
        return self.post(shared,pr,None)

class AsyncNode(Node):
    async def prep_async(self,shared): pass
    async def exec_async(self,prep_res): pass
    async def exec_fallback_async(self,prep_res,exc): raise exc
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
                    return await self.exec_fallback_async(prep_res,e)
                w=self._get_wait_time(i)
                if tracer: tracer.record(TraceEventType.RETRY_WAIT, node_name, {"wait_time": w, "error": str(e)})
                if w>0: await asyncio.sleep(w)
    async def run_async(self,shared,tracer=None):
        if self.successors: warnings.warn("Node won't run successors. Use AsyncFlow.")
        # For standalone node.run_async(), we need to record NODE_START/END here since no orchestrator
        tracer_to_use = tracer or _get_current_tracer()
        node_name = _get_node_name(self) if tracer_to_use else None
        if tracer_to_use: tracer_to_use.record(TraceEventType.NODE_START, node_name)
        result = await self._run_async(shared,tracer)
        if tracer_to_use: tracer_to_use.record(TraceEventType.NODE_END, node_name)
        return result
    async def _run_async(self,shared,tracer=None):
        # Note: NODE_START/NODE_END are recorded at orchestration level (AsyncFlow._orch_async) for defensive tracing
        tracer = tracer or _get_current_tracer()
        node_name = _get_node_name(self) if tracer else None
        p=await self.prep_async(shared)
        if tracer: tracer.record(TraceEventType.NODE_PREP, node_name, {"prep_result": p} if tracer.capture_data else None)
        e=await self._exec(p,tracer)
        if tracer: tracer.record(TraceEventType.NODE_EXEC, node_name, {"exec_result": e} if tracer.capture_data else None)
        result=await self.post_async(shared,p,e)
        if tracer: tracer.record(TraceEventType.NODE_POST, node_name, {"action": result} if result else None)
        return result
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
            last_action = await curr._run_async(shared,tracer) if isinstance(curr,AsyncNode) else curr._run(shared,tracer)
            # Defensive tracing: record NODE_END at orchestration level
            if tracer: tracer.record(TraceEventType.NODE_END, curr_name)
            nxt = self.get_next_node(curr, last_action)
            if tracer and nxt:
                tracer.record(TraceEventType.TRANSITION, flow_name, {
                    "from_node": curr_name,
                    "to_node": _get_node_name(nxt),
                    "action": last_action or "default"
                })
            curr = copy.copy(nxt)
        return last_action
    async def _run_async(self,shared,tracer=None): p=await self.prep_async(shared); o=await self._orch_async(shared,tracer=tracer); return await self.post_async(shared,p,o)
    async def run_async(self,shared,tracer=None):
        token = _set_current_tracer(tracer) if tracer else None
        flow_name = _get_node_name(self)
        if tracer: tracer.record(TraceEventType.FLOW_START, flow_name)
        try:
            if self.successors: warnings.warn("Node won't run successors. Use Flow.")
            result = await self._run_async(shared, tracer)
            if tracer: tracer.record(TraceEventType.FLOW_END, flow_name)
            return result
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
        return await self.post_async(shared,pr,None)

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
        return await self.post_async(shared,pr,None)