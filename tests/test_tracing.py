# tests/test_tracing.py
import unittest
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pocketflow import (
    Node, Flow, AsyncNode, AsyncFlow, BatchFlow,
    FlowTracer, TraceEventType, TraceEvent, NodeTiming, SortByPhase
)


# --- Node Definitions for Testing ---

class NumberNode(Node):
    def __init__(self, number):
        super().__init__()
        self.number = number
        self.name = f"NumberNode({number})"

    def prep(self, shared_storage):
        shared_storage['current'] = self.number


class AddNode(Node):
    def __init__(self, number):
        super().__init__()
        self.number = number
        self.name = f"AddNode({number})"

    def prep(self, shared_storage):
        shared_storage['current'] += self.number


class CheckPositiveNode(Node):
    def __init__(self):
        super().__init__()
        self.name = "CheckPositiveNode"

    def post(self, shared_storage, prep_result, proc_result):
        if shared_storage['current'] >= 0:
            return 'positive'
        else:
            return 'negative'


class FailingNode(Node):
    """Node that fails a certain number of times before succeeding."""
    def __init__(self, fail_count=2, max_retries=3):
        super().__init__(max_retries=max_retries, wait=0.01)
        self.fail_count = fail_count
        self.attempts = 0
        self.name = "FailingNode"

    def exec(self, prep_res):
        self.attempts += 1
        if self.attempts <= self.fail_count:
            raise ValueError(f"Intentional failure {self.attempts}")
        return "success"


class FallbackNode(Node):
    """Node that always fails and uses fallback."""
    def __init__(self):
        super().__init__(max_retries=2, wait=0.01)
        self.name = "FallbackNode"

    def exec(self, prep_res):
        raise ValueError("Always fails")

    def exec_fallback(self, prep_res, exc):
        return "fallback_result"


class AsyncNumberNode(AsyncNode):
    def __init__(self, number):
        super().__init__()
        self.number = number
        self.name = f"AsyncNumberNode({number})"

    async def prep_async(self, shared_storage):
        shared_storage['current'] = self.number


class AsyncAddNode(AsyncNode):
    def __init__(self, number):
        super().__init__()
        self.number = number
        self.name = f"AsyncAddNode({number})"

    async def prep_async(self, shared_storage):
        shared_storage['current'] += self.number


class AsyncCheckPositiveNode(AsyncNode):
    def __init__(self):
        super().__init__()
        self.name = "AsyncCheckPositiveNode"

    async def post_async(self, shared_storage, prep_result, proc_result):
        if shared_storage['current'] >= 0:
            return 'positive'
        else:
            return 'negative'


# --- Test Classes ---

class TestFlowTracer(unittest.TestCase):
    """Test the FlowTracer class itself."""

    def test_tracer_initialization(self):
        """Test tracer initializes with empty events."""
        tracer = FlowTracer()
        self.assertEqual(len(tracer.events), 0)
        self.assertFalse(tracer.capture_data)
        self.assertEqual(tracer.max_data_size, 1000)

    def test_tracer_initialization_with_options(self):
        """Test tracer with custom options."""
        tracer = FlowTracer(capture_data=True, max_data_size=500)
        self.assertTrue(tracer.capture_data)
        self.assertEqual(tracer.max_data_size, 500)

    def test_tracer_record_event(self):
        """Test recording events."""
        tracer = FlowTracer()
        tracer.record(TraceEventType.NODE_START, "TestNode")
        self.assertEqual(len(tracer.events), 1)
        self.assertEqual(tracer.events[0].event_type, TraceEventType.NODE_START)
        self.assertEqual(tracer.events[0].node_name, "TestNode")

    def test_tracer_record_with_data(self):
        """Test recording events with data when capture_data=False."""
        tracer = FlowTracer(capture_data=False)
        tracer.record(TraceEventType.TRANSITION, "Flow", {
            "from_node": "A",
            "to_node": "B",
            "action": "default",
            "extra": "ignored"
        })
        self.assertEqual(len(tracer.events), 1)
        # Only lightweight keys should be captured
        self.assertEqual(tracer.events[0].data["from_node"], "A")
        self.assertEqual(tracer.events[0].data["to_node"], "B")
        self.assertEqual(tracer.events[0].data["action"], "default")
        self.assertNotIn("extra", tracer.events[0].data)

    def test_tracer_record_with_full_data(self):
        """Test recording events with data when capture_data=True."""
        tracer = FlowTracer(capture_data=True)
        tracer.record(TraceEventType.NODE_PREP, "TestNode", {
            "prep_result": {"key": "value"},
            "extra": "captured"
        })
        self.assertEqual(len(tracer.events), 1)
        self.assertIn("prep_result", tracer.events[0].data)
        self.assertIn("extra", tracer.events[0].data)

    def test_tracer_clear(self):
        """Test clearing events."""
        tracer = FlowTracer()
        tracer.record(TraceEventType.NODE_START, "TestNode")
        tracer.record(TraceEventType.NODE_END, "TestNode")
        self.assertEqual(len(tracer.events), 2)
        tracer.clear()
        self.assertEqual(len(tracer.events), 0)

    def test_tracer_duration(self):
        """Test duration calculation."""
        tracer = FlowTracer()
        self.assertEqual(tracer.get_duration(), 0.0)
        tracer.record(TraceEventType.FLOW_START, "Flow")
        import time
        time.sleep(0.01)
        tracer.record(TraceEventType.FLOW_END, "Flow")
        duration = tracer.get_duration()
        self.assertGreater(duration, 0.0)

    def test_tracer_to_dict(self):
        """Test exporting trace as dictionary."""
        tracer = FlowTracer()
        tracer.record(TraceEventType.FLOW_START, "Flow")
        tracer.record(TraceEventType.NODE_START, "NodeA")
        tracer.record(TraceEventType.NODE_END, "NodeA")
        tracer.record(TraceEventType.FLOW_END, "Flow")

        result = tracer.to_dict()
        self.assertIn("duration", result)
        self.assertIn("execution_order", result)
        self.assertIn("transitions", result)
        self.assertIn("retries", result)
        self.assertIn("events", result)
        self.assertEqual(len(result["events"]), 4)


class TestFlowTracingBasic(unittest.TestCase):
    """Test basic flow tracing functionality."""

    def test_simple_flow_tracing(self):
        """Test tracing a simple linear flow."""
        shared = {}
        n1 = NumberNode(5)
        n2 = AddNode(3)

        flow = Flow()
        flow.start(n1) >> n2

        tracer = FlowTracer()
        result = flow.run(shared, tracer=tracer)

        self.assertEqual(shared['current'], 8)

        # Check execution order
        execution_order = tracer.get_execution_order()
        self.assertEqual(execution_order, ["NumberNode(5)", "AddNode(3)"])

        # Check we have the expected events
        event_types = [e.event_type for e in tracer.events]
        self.assertIn(TraceEventType.FLOW_START, event_types)
        self.assertIn(TraceEventType.FLOW_END, event_types)
        self.assertIn(TraceEventType.NODE_START, event_types)
        self.assertIn(TraceEventType.NODE_END, event_types)
        self.assertIn(TraceEventType.TRANSITION, event_types)

    def test_branching_flow_tracing(self):
        """Test tracing a flow with conditional branching."""
        shared = {}
        start_node = NumberNode(5)
        check_node = CheckPositiveNode()
        add_positive = AddNode(10)
        add_negative = AddNode(-20)

        flow = Flow()
        flow.start(start_node) >> check_node
        check_node - "positive" >> add_positive
        check_node - "negative" >> add_negative

        tracer = FlowTracer()
        flow.run(shared, tracer=tracer)

        self.assertEqual(shared['current'], 15)

        # Check transitions
        transitions = tracer.get_transitions()
        self.assertEqual(len(transitions), 2)

        # First transition: NumberNode -> CheckPositiveNode (default)
        self.assertEqual(transitions[0]["from"], "NumberNode(5)")
        self.assertEqual(transitions[0]["to"], "CheckPositiveNode")
        self.assertEqual(transitions[0]["action"], "default")

        # Second transition: CheckPositiveNode -> AddNode(10) (positive)
        self.assertEqual(transitions[1]["from"], "CheckPositiveNode")
        self.assertEqual(transitions[1]["to"], "AddNode(10)")
        self.assertEqual(transitions[1]["action"], "positive")

    def test_negative_branch_tracing(self):
        """Test tracing negative branch."""
        shared = {}
        start_node = NumberNode(-5)
        check_node = CheckPositiveNode()
        add_positive = AddNode(10)
        add_negative = AddNode(-20)

        flow = Flow()
        flow.start(start_node) >> check_node
        check_node - "positive" >> add_positive
        check_node - "negative" >> add_negative

        tracer = FlowTracer()
        flow.run(shared, tracer=tracer)

        self.assertEqual(shared['current'], -25)

        transitions = tracer.get_transitions()
        # Should take negative branch
        self.assertEqual(transitions[1]["action"], "negative")
        self.assertEqual(transitions[1]["to"], "AddNode(-20)")


class TestFlowTracingRetries(unittest.TestCase):
    """Test tracing with retry logic."""

    def test_retry_tracing(self):
        """Test that retries are recorded."""
        shared = {}
        failing_node = FailingNode(fail_count=2, max_retries=3)

        flow = Flow()
        flow.start(failing_node)

        tracer = FlowTracer()
        flow.run(shared, tracer=tracer)

        # Check retries were recorded
        retries = tracer.get_retries()
        self.assertEqual(len(retries), 3)  # 3 retry attempts recorded
        self.assertEqual(retries[0]["retry"], 1)
        self.assertEqual(retries[1]["retry"], 2)
        self.assertEqual(retries[2]["retry"], 3)

    def test_fallback_tracing(self):
        """Test that fallback is recorded."""
        shared = {}
        fallback_node = FallbackNode()

        flow = Flow()
        flow.start(fallback_node)

        tracer = FlowTracer()
        flow.run(shared, tracer=tracer)

        # Check fallback was recorded
        event_types = [e.event_type for e in tracer.events]
        self.assertIn(TraceEventType.FALLBACK, event_types)

        # Check retry waits were recorded
        wait_events = [e for e in tracer.events if e.event_type == TraceEventType.RETRY_WAIT]
        self.assertEqual(len(wait_events), 1)  # One wait between retries


class TestFlowTracingDataCapture(unittest.TestCase):
    """Test tracing with data capture enabled."""

    def test_data_capture_enabled(self):
        """Test that data is captured when enabled."""
        shared = {}
        n1 = NumberNode(5)

        flow = Flow()
        flow.start(n1)

        tracer = FlowTracer(capture_data=True)
        flow.run(shared, tracer=tracer)

        # Check that prep result is captured
        prep_events = [e for e in tracer.events if e.event_type == TraceEventType.NODE_PREP]
        self.assertTrue(len(prep_events) > 0)
        # Data should be captured (even if None for this node)
        self.assertIsNotNone(prep_events[0].data)

    def test_data_truncation(self):
        """Test that large data is truncated."""
        tracer = FlowTracer(capture_data=True, max_data_size=20)
        large_data = "x" * 100
        tracer.record(TraceEventType.NODE_PREP, "TestNode", {"data": large_data})

        self.assertIn("[truncated]", tracer.events[0].data["data"])


class TestAsyncFlowTracing(unittest.TestCase):
    """Test tracing for async flows."""

    def test_async_flow_tracing(self):
        """Test tracing an async flow."""
        async def run_test():
            shared = {}
            n1 = AsyncNumberNode(5)
            n2 = AsyncAddNode(3)

            flow = AsyncFlow()
            flow.start(n1) >> n2

            tracer = FlowTracer()
            result = await flow.run_async(shared, tracer=tracer)

            self.assertEqual(shared['current'], 8)

            # Check execution order
            execution_order = tracer.get_execution_order()
            self.assertEqual(execution_order, ["AsyncNumberNode(5)", "AsyncAddNode(3)"])

            # Check we have the expected events
            event_types = [e.event_type for e in tracer.events]
            self.assertIn(TraceEventType.FLOW_START, event_types)
            self.assertIn(TraceEventType.FLOW_END, event_types)
            self.assertIn(TraceEventType.TRANSITION, event_types)

        asyncio.run(run_test())

    def test_async_branching_tracing(self):
        """Test tracing async flow with branching."""
        async def run_test():
            shared = {}
            start_node = AsyncNumberNode(5)
            check_node = AsyncCheckPositiveNode()
            add_positive = AsyncAddNode(10)
            add_negative = AsyncAddNode(-20)

            flow = AsyncFlow()
            flow.start(start_node) >> check_node
            check_node - "positive" >> add_positive
            check_node - "negative" >> add_negative

            tracer = FlowTracer()
            await flow.run_async(shared, tracer=tracer)

            self.assertEqual(shared['current'], 15)

            transitions = tracer.get_transitions()
            self.assertEqual(transitions[1]["action"], "positive")

        asyncio.run(run_test())


class TestTracerNoPerformanceImpact(unittest.TestCase):
    """Test that tracing is optional and doesn't impact normal flow."""

    def test_flow_without_tracer(self):
        """Test that flows work normally without a tracer."""
        shared = {}
        n1 = NumberNode(5)
        n2 = AddNode(3)

        flow = Flow()
        flow.start(n1) >> n2

        # Run without tracer
        result = flow.run(shared)
        self.assertEqual(shared['current'], 8)
        self.assertIsNone(result)

    def test_node_run_without_tracer(self):
        """Test individual node run without tracer."""
        shared = {}
        n1 = NumberNode(5)

        result = n1.run(shared)
        self.assertEqual(shared['current'], 5)


class TestTracerHelpers(unittest.TestCase):
    """Test tracer helper methods."""

    def test_execution_order(self):
        """Test get_execution_order helper."""
        shared = {}
        n1 = NumberNode(5)
        n2 = AddNode(3)
        n3 = AddNode(2)

        flow = Flow()
        flow.start(n1) >> n2 >> n3

        tracer = FlowTracer()
        flow.run(shared, tracer=tracer)

        order = tracer.get_execution_order()
        self.assertEqual(len(order), 3)
        self.assertEqual(order[0], "NumberNode(5)")
        self.assertEqual(order[1], "AddNode(3)")
        self.assertEqual(order[2], "AddNode(2)")

    def test_print_summary_no_error(self):
        """Test that print_summary doesn't raise errors."""
        shared = {}
        n1 = NumberNode(5)

        flow = Flow()
        flow.start(n1)

        tracer = FlowTracer()
        flow.run(shared, tracer=tracer)

        # This should not raise any errors
        import io
        import sys
        captured = io.StringIO()
        sys.stdout = captured
        try:
            tracer.print_summary()
        finally:
            sys.stdout = sys.__stdout__

        output = captured.getvalue()
        self.assertIn("FLOW EXECUTION TRACE", output)
        self.assertIn("NumberNode(5)", output)


class TestTraceEvent(unittest.TestCase):
    """Test TraceEvent dataclass."""

    def test_trace_event_repr(self):
        """Test TraceEvent string representation."""
        event = TraceEvent(TraceEventType.NODE_START, "TestNode")
        repr_str = repr(event)
        self.assertIn("node_start", repr_str)
        self.assertIn("TestNode", repr_str)

    def test_trace_event_with_data(self):
        """Test TraceEvent with data."""
        event = TraceEvent(
            TraceEventType.TRANSITION,
            "Flow",
            data={"from_node": "A", "to_node": "B"}
        )
        repr_str = repr(event)
        self.assertIn("from_node", repr_str)


class TestTracerAsyncSafety(unittest.TestCase):
    """Test that tracing is async-safe with contextvars."""

    def test_parallel_async_flows_isolated_tracers(self):
        """Test that parallel async flows have isolated tracers."""
        async def run_test():
            # Create two tracers for two parallel flows
            tracer1 = FlowTracer()
            tracer2 = FlowTracer()

            shared1 = {}
            shared2 = {}

            n1 = AsyncNumberNode(10)
            n1.name = "Flow1_Number"
            n2 = AsyncAddNode(5)
            n2.name = "Flow1_Add"

            n3 = AsyncNumberNode(100)
            n3.name = "Flow2_Number"
            n4 = AsyncAddNode(50)
            n4.name = "Flow2_Add"

            flow1 = AsyncFlow()
            flow1.name = "Flow1"
            flow1.start(n1) >> n2

            flow2 = AsyncFlow()
            flow2.name = "Flow2"
            flow2.start(n3) >> n4

            # Run both flows concurrently with different tracers
            await asyncio.gather(
                flow1.run_async(shared1, tracer=tracer1),
                flow2.run_async(shared2, tracer=tracer2)
            )

            # Each tracer should only have events from its own flow
            flow1_events = [e.node_name for e in tracer1.events]
            flow2_events = [e.node_name for e in tracer2.events]

            # Flow1 events should not contain Flow2 node names
            self.assertNotIn("Flow2_Number", flow1_events)
            self.assertNotIn("Flow2_Add", flow1_events)

            # Flow2 events should not contain Flow1 node names
            self.assertNotIn("Flow1_Number", flow2_events)
            self.assertNotIn("Flow1_Add", flow2_events)

            # Each tracer should have the correct events
            self.assertIn("Flow1", flow1_events)
            self.assertIn("Flow2", flow2_events)

            # Results should be correct
            self.assertEqual(shared1['current'], 15)
            self.assertEqual(shared2['current'], 150)

        asyncio.run(run_test())

    def test_nested_flows_with_tracer(self):
        """Test that nested flows share the same tracer correctly."""
        shared = {}

        # Create inner flow
        inner_node1 = NumberNode(5)
        inner_node1.name = "Inner_Number"
        inner_node2 = AddNode(3)
        inner_node2.name = "Inner_Add"

        inner_flow = Flow()
        inner_flow.name = "InnerFlow"
        inner_flow.start(inner_node1) >> inner_node2

        # Create outer flow that uses inner flow as a node
        outer_node = AddNode(10)
        outer_node.name = "Outer_Add"

        outer_flow = Flow()
        outer_flow.name = "OuterFlow"
        outer_flow.start(inner_flow) >> outer_node

        tracer = FlowTracer()
        outer_flow.run(shared, tracer=tracer)

        # The result should be correct: 5 + 3 + 10 = 18
        self.assertEqual(shared['current'], 18)

        # The tracer should have events from both flows
        event_names = [e.node_name for e in tracer.events]
        self.assertIn("OuterFlow", event_names)
        self.assertIn("InnerFlow", event_names)


class TestTracerNodeNameCaching(unittest.TestCase):
    """Test that node name caching works correctly."""

    def test_node_name_cached(self):
        """Test that node name is cached after first access."""
        node = NumberNode(5)
        node.name = "TestNode"

        # First access should cache
        name1 = node._cached_trace_name
        self.assertIsNone(name1)  # Not cached yet

        # Access through _get_node_name
        from pocketflow import _get_node_name
        name2 = _get_node_name(node)
        self.assertEqual(name2, "TestNode")

        # Now it should be cached
        self.assertEqual(node._cached_trace_name, "TestNode")

        # Change the name - cached value should NOT change (this is the tradeoff for performance)
        node.name = "ChangedName"
        name3 = _get_node_name(node)
        self.assertEqual(name3, "TestNode")  # Still returns cached value

    def test_class_name_fallback(self):
        """Test that class name is used when no custom name is set."""
        # NumberNode in this test file sets self.name in __init__, so use a plain Node
        from pocketflow import Node
        node = Node()

        from pocketflow import _get_node_name
        name = _get_node_name(node)
        self.assertEqual(name, "Node")


class TestTracerDefensiveProgramming(unittest.TestCase):
    """Test that tracing works even with custom _run overrides."""

    def test_custom_run_override_still_traces(self):
        """Test that a custom node with _run override is still traced at orchestration level."""
        class CustomRunNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "CustomRunNode"

            def _run(self, shared, tracer=None):
                # Custom _run that doesn't call super or do any tracing
                shared['custom'] = True
                return "custom_action"

        shared = {}
        custom_node = CustomRunNode()
        next_node = NumberNode(5)
        next_node.name = "NextNode"

        flow = Flow()
        flow.start(custom_node)
        custom_node - "custom_action" >> next_node

        tracer = FlowTracer()
        flow.run(shared, tracer=tracer)

        # The custom node should still be traced at orchestration level
        execution_order = tracer.get_execution_order()
        self.assertIn("CustomRunNode", execution_order)
        self.assertIn("NextNode", execution_order)

        # Verify the custom node ran
        self.assertTrue(shared.get('custom'))


class TestNodeTiming(unittest.TestCase):
    """Test node timing measurement functionality."""

    def test_node_timing_basic(self):
        """Test that basic timing is recorded for nodes."""
        import time
        from pocketflow import NodeTiming

        class SlowNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "SlowNode"

            def prep(self, shared):
                time.sleep(0.01)
                return "prep_done"

            def exec(self, prep_res):
                time.sleep(0.02)
                return "exec_done"

            def post(self, shared, prep_res, exec_res):
                time.sleep(0.01)
                return None

        node = SlowNode()
        flow = Flow()
        flow.start(node)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        timings = tracer.get_node_timings()
        self.assertEqual(len(timings), 1)

        timing = timings[0]
        self.assertEqual(timing.node_name, "SlowNode")
        self.assertIsNotNone(timing.prep_time)
        self.assertIsNotNone(timing.exec_time)
        self.assertIsNotNone(timing.post_time)
        self.assertIsNotNone(timing.total_time)

        # Verify timing values are reasonable (at least the sleep durations)
        self.assertGreaterEqual(timing.prep_time, 0.01)
        self.assertGreaterEqual(timing.exec_time, 0.02)
        self.assertGreaterEqual(timing.post_time, 0.01)
        self.assertGreaterEqual(timing.total_time, 0.04)

    def test_get_slowest_node(self):
        """Test getting the slowest node from a flow."""
        import time

        class FastNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "FastNode"

            def exec(self, prep_res):
                time.sleep(0.01)
                return "fast"

        class SlowNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "SlowNode"

            def exec(self, prep_res):
                time.sleep(0.05)
                return "slow"

        fast = FastNode()
        slow = SlowNode()
        fast >> slow

        flow = Flow()
        flow.start(fast)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        slowest = tracer.get_slowest_node()
        self.assertIsNotNone(slowest)
        self.assertEqual(slowest.node_name, "SlowNode")
        self.assertGreaterEqual(slowest.total_time, 0.05)

    def test_get_node_timings_multiple_nodes(self):
        """Test timing for multiple nodes in a flow."""
        node1 = NumberNode(1)
        node2 = AddNode(2)
        node3 = AddNode(3)
        node1 >> node2 >> node3

        flow = Flow()
        flow.start(node1)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        timings = tracer.get_node_timings()
        self.assertEqual(len(timings), 3)

        node_names = [t.node_name for t in timings]
        self.assertEqual(node_names, ["NumberNode(1)", "AddNode(2)", "AddNode(3)"])

        # All timings should have total_time
        for t in timings:
            self.assertIsNotNone(t.total_time)
            self.assertGreater(t.total_time, 0)

    def test_timing_in_to_dict(self):
        """Test that timing info is included in to_dict output."""
        import time

        class TimedNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "TimedNode"

            def exec(self, prep_res):
                time.sleep(0.01)
                return "done"

        node = TimedNode()
        flow = Flow()
        flow.start(node)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        result = tracer.to_dict()

        # Check node_timings key exists
        self.assertIn("node_timings", result)
        self.assertEqual(len(result["node_timings"]), 1)

        timing = result["node_timings"][0]
        self.assertEqual(timing["node_name"], "TimedNode")
        self.assertIn("prep_time", timing)
        self.assertIn("exec_time", timing)
        self.assertIn("post_time", timing)
        self.assertIn("total_time", timing)

        # Check slowest_node key exists
        self.assertIn("slowest_node", result)
        self.assertEqual(result["slowest_node"]["node_name"], "TimedNode")

    def test_timing_in_events_data(self):
        """Test that timing info is captured in event data."""
        node = NumberNode(5)
        flow = Flow()
        flow.start(node)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        # Find NODE_PREP, NODE_EXEC, NODE_POST events
        prep_events = [e for e in tracer.events if e.event_type == TraceEventType.NODE_PREP]
        exec_events = [e for e in tracer.events if e.event_type == TraceEventType.NODE_EXEC]
        post_events = [e for e in tracer.events if e.event_type == TraceEventType.NODE_POST]

        self.assertEqual(len(prep_events), 1)
        self.assertEqual(len(exec_events), 1)
        self.assertEqual(len(post_events), 1)

        # Check timing data is recorded
        self.assertIn("prep_time", prep_events[0].data)
        self.assertIn("exec_time", exec_events[0].data)
        self.assertIn("post_time", post_events[0].data)

    def test_get_slowest_node_empty_tracer(self):
        """Test get_slowest_node returns None for empty tracer."""
        tracer = FlowTracer()
        slowest = tracer.get_slowest_node()
        self.assertIsNone(slowest)

    def test_get_node_timings_empty_tracer(self):
        """Test get_node_timings returns empty list for empty tracer."""
        tracer = FlowTracer()
        timings = tracer.get_node_timings()
        self.assertEqual(timings, [])

    def test_print_timing_table_no_crash(self):
        """Test that print_timing_table doesn't crash."""
        import io
        import sys

        # Capture stdout
        captured_output = io.StringIO()
        sys.stdout = captured_output

        try:
            # Empty tracer
            tracer = FlowTracer()
            tracer.print_timing_table()

            # Tracer with data
            node = NumberNode(5)
            flow = Flow()
            flow.start(node)
            tracer2 = FlowTracer()
            flow.run({}, tracer=tracer2)
            tracer2.print_timing_table()

            output = captured_output.getvalue()
            self.assertIn("No timing data recorded", output)
            self.assertIn("NODE TIMING TABLE", output)
            self.assertIn("NumberNode(5)", output)
        finally:
            sys.stdout = sys.__stdout__


class TestAsyncNodeTiming(unittest.TestCase):
    """Test async node timing measurement functionality."""

    def test_async_node_timing(self):
        """Test that timing is recorded for async nodes."""
        import time

        class SlowAsyncNode(AsyncNode):
            def __init__(self):
                super().__init__()
                self.name = "SlowAsyncNode"

            async def prep_async(self, shared):
                await asyncio.sleep(0.01)
                return "prep_done"

            async def exec_async(self, prep_res):
                await asyncio.sleep(0.02)
                return "exec_done"

            async def post_async(self, shared, prep_res, exec_res):
                await asyncio.sleep(0.01)
                return None

        async def run_test():
            node = SlowAsyncNode()
            flow = AsyncFlow()
            flow.start(node)

            tracer = FlowTracer()
            await flow.run_async({}, tracer=tracer)

            return tracer

        tracer = asyncio.run(run_test())

        timings = tracer.get_node_timings()
        self.assertEqual(len(timings), 1)

        timing = timings[0]
        self.assertEqual(timing.node_name, "SlowAsyncNode")
        self.assertIsNotNone(timing.prep_time)
        self.assertIsNotNone(timing.exec_time)
        self.assertIsNotNone(timing.post_time)
        self.assertIsNotNone(timing.total_time)

        # Verify timing values are reasonable
        self.assertGreaterEqual(timing.prep_time, 0.01)
        self.assertGreaterEqual(timing.exec_time, 0.02)
        self.assertGreaterEqual(timing.post_time, 0.01)
        self.assertGreaterEqual(timing.total_time, 0.04)

    def test_async_flow_multiple_nodes_timing(self):
        """Test timing for multiple async nodes in a flow."""
        async def run_test():
            node1 = AsyncNumberNode(1)
            node2 = AsyncAddNode(2)
            node1 >> node2

            flow = AsyncFlow()
            flow.start(node1)

            tracer = FlowTracer()
            await flow.run_async({}, tracer=tracer)

            return tracer

        tracer = asyncio.run(run_test())

        timings = tracer.get_node_timings()
        self.assertEqual(len(timings), 2)

        for t in timings:
            self.assertIsNotNone(t.total_time)
            self.assertIsNotNone(t.prep_time)


class TestTimingIntegration(unittest.TestCase):
    """Test timing integration with print_summary and to_dict."""

    def test_print_summary_includes_timing(self):
        """Test that print_summary includes timing info."""
        import io
        import sys
        import time

        class TimedNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "TimedNode"

            def exec(self, prep_res):
                time.sleep(0.01)
                return "done"

        node = TimedNode()
        flow = Flow()
        flow.start(node)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        # Capture stdout
        captured_output = io.StringIO()
        sys.stdout = captured_output

        try:
            tracer.print_summary()
            output = captured_output.getvalue()

            # Check timing section exists
            self.assertIn("Node timings:", output)
            self.assertIn("TimedNode:", output)
            self.assertIn("prep=", output)
            self.assertIn("exec=", output)
            self.assertIn("post=", output)
            self.assertIn("Slowest:", output)
        finally:
            sys.stdout = sys.__stdout__

    def test_to_dict_timing_serializable(self):
        """Test that timing data in to_dict is JSON-serializable."""
        import json

        node = NumberNode(5)
        flow = Flow()
        flow.start(node)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        result = tracer.to_dict()

        # Should not raise an exception
        json_str = json.dumps(result)
        self.assertIsInstance(json_str, str)

        # Parse back and verify structure
        parsed = json.loads(json_str)
        self.assertIn("node_timings", parsed)
        self.assertIn("slowest_node", parsed)


class TestGetSlowestNodes(unittest.TestCase):
    """Test the get_slowest_nodes() method with various sort options."""

    def test_get_slowest_nodes_default(self):
        """Test get_slowest_nodes returns all nodes sorted by total time."""
        import time

        class FastNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "FastNode"

            def exec(self, prep_res):
                time.sleep(0.01)
                return "fast"

        class MediumNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "MediumNode"

            def exec(self, prep_res):
                time.sleep(0.03)
                return "medium"

        class SlowNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "SlowNode"

            def exec(self, prep_res):
                time.sleep(0.05)
                return "slow"

        fast = FastNode()
        medium = MediumNode()
        slow = SlowNode()
        fast >> medium >> slow

        flow = Flow()
        flow.start(fast)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        # Get all nodes sorted by total time (slowest first)
        slowest = tracer.get_slowest_nodes()
        self.assertEqual(len(slowest), 3)
        self.assertEqual(slowest[0].node_name, "SlowNode")
        self.assertEqual(slowest[1].node_name, "MediumNode")
        self.assertEqual(slowest[2].node_name, "FastNode")

    def test_get_slowest_nodes_with_limit(self):
        """Test get_slowest_nodes with n parameter limits results."""
        import time

        class NodeA(Node):
            def __init__(self):
                super().__init__()
                self.name = "NodeA"
            def exec(self, prep_res):
                time.sleep(0.01)

        class NodeB(Node):
            def __init__(self):
                super().__init__()
                self.name = "NodeB"
            def exec(self, prep_res):
                time.sleep(0.02)

        class NodeC(Node):
            def __init__(self):
                super().__init__()
                self.name = "NodeC"
            def exec(self, prep_res):
                time.sleep(0.03)

        a, b, c = NodeA(), NodeB(), NodeC()
        a >> b >> c

        flow = Flow()
        flow.start(a)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        # Get only top 2 slowest
        top2 = tracer.get_slowest_nodes(n=2)
        self.assertEqual(len(top2), 2)
        self.assertEqual(top2[0].node_name, "NodeC")
        self.assertEqual(top2[1].node_name, "NodeB")

    def test_get_slowest_nodes_sort_by_prep(self):
        """Test sorting by prep time."""
        import time

        class SlowPrepNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "SlowPrepNode"

            def prep(self, shared):
                time.sleep(0.03)
                return "prep"

            def exec(self, prep_res):
                time.sleep(0.01)
                return "exec"

        class FastPrepNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "FastPrepNode"

            def prep(self, shared):
                time.sleep(0.01)
                return "prep"

            def exec(self, prep_res):
                time.sleep(0.03)  # Slow exec but fast prep
                return "exec"

        slow_prep = SlowPrepNode()
        fast_prep = FastPrepNode()
        slow_prep >> fast_prep

        flow = Flow()
        flow.start(slow_prep)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        # Sort by prep time
        by_prep = tracer.get_slowest_nodes(sort_by='prep')
        self.assertEqual(by_prep[0].node_name, "SlowPrepNode")
        self.assertGreater(by_prep[0].prep_time, by_prep[1].prep_time)

    def test_get_slowest_nodes_sort_by_exec(self):
        """Test sorting by exec time."""
        import time

        class SlowExecNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "SlowExecNode"

            def exec(self, prep_res):
                time.sleep(0.04)
                return "slow_exec"

        class FastExecNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "FastExecNode"

            def exec(self, prep_res):
                time.sleep(0.01)
                return "fast_exec"

        slow_exec = SlowExecNode()
        fast_exec = FastExecNode()
        slow_exec >> fast_exec

        flow = Flow()
        flow.start(slow_exec)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        # Sort by exec time
        by_exec = tracer.get_slowest_nodes(sort_by='exec')
        self.assertEqual(by_exec[0].node_name, "SlowExecNode")
        self.assertGreater(by_exec[0].exec_time, by_exec[1].exec_time)

    def test_get_slowest_nodes_sort_by_post(self):
        """Test sorting by post time."""
        import time

        class SlowPostNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "SlowPostNode"

            def post(self, shared, prep_res, exec_res):
                time.sleep(0.03)
                return None

        class FastPostNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "FastPostNode"

            def post(self, shared, prep_res, exec_res):
                time.sleep(0.01)
                return None

        slow_post = SlowPostNode()
        fast_post = FastPostNode()
        slow_post >> fast_post

        flow = Flow()
        flow.start(slow_post)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        # Sort by post time
        by_post = tracer.get_slowest_nodes(sort_by='post')
        self.assertEqual(by_post[0].node_name, "SlowPostNode")
        self.assertGreater(by_post[0].post_time, by_post[1].post_time)

    def test_get_slowest_nodes_invalid_sort_by(self):
        """Test that invalid sort_by raises ValueError."""
        node = NumberNode(5)
        flow = Flow()
        flow.start(node)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        with self.assertRaises(ValueError) as context:
            tracer.get_slowest_nodes(sort_by='invalid')

        self.assertIn("sort_by must be one of", str(context.exception))
        self.assertIn("'invalid'", str(context.exception))

    def test_get_slowest_node_invalid_sort_by(self):
        """Test that get_slowest_node with invalid sort_by raises ValueError."""
        node = NumberNode(5)
        flow = Flow()
        flow.start(node)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        with self.assertRaises(ValueError) as context:
            tracer.get_slowest_node(sort_by='bad_value')

        self.assertIn("sort_by must be one of", str(context.exception))

    def test_get_slowest_nodes_empty_tracer(self):
        """Test get_slowest_nodes returns empty list for empty tracer."""
        tracer = FlowTracer()
        slowest = tracer.get_slowest_nodes()
        self.assertEqual(slowest, [])

    def test_get_slowest_node_with_sort_by(self):
        """Test get_slowest_node respects sort_by parameter."""
        import time

        class PrepHeavyNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "PrepHeavyNode"

            def prep(self, shared):
                time.sleep(0.04)
                return "prep"

            def exec(self, prep_res):
                time.sleep(0.01)
                return "exec"

        class ExecHeavyNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "ExecHeavyNode"

            def prep(self, shared):
                time.sleep(0.01)
                return "prep"

            def exec(self, prep_res):
                time.sleep(0.04)
                return "exec"

        prep_heavy = PrepHeavyNode()
        exec_heavy = ExecHeavyNode()
        prep_heavy >> exec_heavy

        flow = Flow()
        flow.start(prep_heavy)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        # Slowest by prep
        slowest_prep = tracer.get_slowest_node(sort_by='prep')
        self.assertEqual(slowest_prep.node_name, "PrepHeavyNode")

        # Slowest by exec
        slowest_exec = tracer.get_slowest_node(sort_by='exec')
        self.assertEqual(slowest_exec.node_name, "ExecHeavyNode")


class TestTimingEdgeCases(unittest.TestCase):
    """Test timing with edge cases like failures and missing phases."""

    def test_timing_with_node_failure_and_retry(self):
        """Test timing is recorded even when node fails and retries."""
        import time

        class FailingTimedNode(Node):
            def __init__(self):
                super().__init__(max_retries=3, wait=0.01)
                self.name = "FailingTimedNode"
                self.attempts = 0

            def prep(self, shared):
                time.sleep(0.01)
                return "prep"

            def exec(self, prep_res):
                self.attempts += 1
                time.sleep(0.02)
                if self.attempts < 3:
                    raise ValueError("Intentional failure")
                return "success"

            def post(self, shared, prep_res, exec_res):
                time.sleep(0.01)
                return None

        node = FailingTimedNode()
        flow = Flow()
        flow.start(node)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        timings = tracer.get_node_timings()
        self.assertEqual(len(timings), 1)

        timing = timings[0]
        self.assertEqual(timing.node_name, "FailingTimedNode")
        # Timing should still be recorded
        self.assertIsNotNone(timing.prep_time)
        self.assertIsNotNone(timing.exec_time)
        self.assertIsNotNone(timing.post_time)
        self.assertIsNotNone(timing.total_time)

        # Exec time should include retry attempts
        self.assertGreaterEqual(timing.exec_time, 0.04)  # At least 2 failed + 1 success

    def test_timing_with_node_failure_fallback(self):
        """Test timing when node fails all retries and uses fallback."""
        import time

        class AlwaysFailingNode(Node):
            def __init__(self):
                super().__init__(max_retries=2, wait=0.01)
                self.name = "AlwaysFailingNode"

            def prep(self, shared):
                time.sleep(0.01)
                return "prep"

            def exec(self, prep_res):
                time.sleep(0.02)
                raise ValueError("Always fails")

            def exec_fallback(self, prep_res, exc):
                return "fallback_result"

            def post(self, shared, prep_res, exec_res):
                time.sleep(0.01)
                return None

        node = AlwaysFailingNode()
        flow = Flow()
        flow.start(node)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        timings = tracer.get_node_timings()
        self.assertEqual(len(timings), 1)

        timing = timings[0]
        # Timing should still be recorded even with fallback
        self.assertIsNotNone(timing.prep_time)
        self.assertIsNotNone(timing.exec_time)
        self.assertIsNotNone(timing.post_time)
        self.assertIsNotNone(timing.total_time)

    def test_timing_with_custom_run_override(self):
        """Test that timing works with custom _run override (no phase timing)."""
        class CustomRunNode(Node):
            def __init__(self):
                super().__init__()
                self.name = "CustomRunNode"

            def _run(self, shared, tracer=None):
                # Custom _run that doesn't call super - no phase timing recorded
                import time
                time.sleep(0.02)
                shared['custom'] = True
                return ("done", None)

        node = CustomRunNode()
        flow = Flow()
        flow.start(node)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        timings = tracer.get_node_timings()
        self.assertEqual(len(timings), 1)

        timing = timings[0]
        self.assertEqual(timing.node_name, "CustomRunNode")
        # Phase timings will be None since custom _run doesn't record them
        self.assertIsNone(timing.prep_time)
        self.assertIsNone(timing.exec_time)
        self.assertIsNone(timing.post_time)
        # But total_time should still be recorded from NODE_START/END
        self.assertIsNotNone(timing.total_time)
        self.assertGreaterEqual(timing.total_time, 0.02)

    def test_timing_nodes_with_none_phase_values_in_sort(self):
        """Test sorting handles None values correctly (treats as 0)."""
        class CustomRunNode(Node):
            def __init__(self, name):
                super().__init__()
                self.name = name

            def _run(self, shared, tracer=None):
                # No phase timing recorded - return None action to transition to default
                return (None, None)

        class NormalNode(Node):
            def __init__(self, name):
                super().__init__()
                self.name = name

            def exec(self, prep_res):
                import time
                time.sleep(0.01)
                return "done"

        custom = CustomRunNode("CustomNode")
        normal = NormalNode("NormalNode")
        custom >> normal

        flow = Flow()
        flow.start(custom)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        # Sort by exec - CustomNode has None exec_time (treated as 0)
        by_exec = tracer.get_slowest_nodes(sort_by='exec')
        self.assertEqual(len(by_exec), 2)
        # NormalNode should be first (has actual exec time)
        self.assertEqual(by_exec[0].node_name, "NormalNode")
        # CustomNode has None exec_time
        self.assertIsNone(by_exec[1].exec_time)

    def test_timing_single_node(self):
        """Test timing with a single node."""
        node = NumberNode(42)
        flow = Flow()
        flow.start(node)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        timings = tracer.get_node_timings()
        self.assertEqual(len(timings), 1)

        # get_slowest_nodes should return single item
        slowest = tracer.get_slowest_nodes()
        self.assertEqual(len(slowest), 1)
        self.assertEqual(slowest[0].node_name, "NumberNode(42)")

    def test_n_greater_than_total_nodes(self):
        """Test get_slowest_nodes when n > number of nodes."""
        node = NumberNode(1)
        flow = Flow()
        flow.start(node)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        # Request 10 slowest but only 1 node exists
        slowest = tracer.get_slowest_nodes(n=10)
        self.assertEqual(len(slowest), 1)


class TestAsyncTimingEdgeCases(unittest.TestCase):
    """Test async timing edge cases."""

    def test_async_timing_with_failure_and_retry(self):
        """Test async timing is recorded even with failures."""
        class FailingAsyncNode(AsyncNode):
            def __init__(self):
                super().__init__(max_retries=3, wait=0.01)
                self.name = "FailingAsyncNode"
                self.attempts = 0

            async def prep_async(self, shared):
                await asyncio.sleep(0.01)
                return "prep"

            async def exec_async(self, prep_res):
                self.attempts += 1
                await asyncio.sleep(0.01)
                if self.attempts < 2:
                    raise ValueError("Intentional failure")
                return "success"

            async def post_async(self, shared, prep_res, exec_res):
                await asyncio.sleep(0.01)
                return None

        async def run_test():
            node = FailingAsyncNode()
            flow = AsyncFlow()
            flow.start(node)

            tracer = FlowTracer()
            await flow.run_async({}, tracer=tracer)

            return tracer

        tracer = asyncio.run(run_test())

        timings = tracer.get_node_timings()
        self.assertEqual(len(timings), 1)

        timing = timings[0]
        self.assertIsNotNone(timing.prep_time)
        self.assertIsNotNone(timing.exec_time)
        self.assertIsNotNone(timing.post_time)
        self.assertIsNotNone(timing.total_time)

    def test_async_get_slowest_nodes_sort(self):
        """Test get_slowest_nodes with async nodes."""
        class SlowPrepAsync(AsyncNode):
            def __init__(self):
                super().__init__()
                self.name = "SlowPrepAsync"

            async def prep_async(self, shared):
                await asyncio.sleep(0.03)
                return "prep"

            async def exec_async(self, prep_res):
                await asyncio.sleep(0.01)
                return "exec"

        class SlowExecAsync(AsyncNode):
            def __init__(self):
                super().__init__()
                self.name = "SlowExecAsync"

            async def prep_async(self, shared):
                await asyncio.sleep(0.01)
                return "prep"

            async def exec_async(self, prep_res):
                await asyncio.sleep(0.03)
                return "exec"

        async def run_test():
            slow_prep = SlowPrepAsync()
            slow_exec = SlowExecAsync()
            slow_prep >> slow_exec

            flow = AsyncFlow()
            flow.start(slow_prep)

            tracer = FlowTracer()
            await flow.run_async({}, tracer=tracer)

            return tracer

        tracer = asyncio.run(run_test())

        # By prep time
        by_prep = tracer.get_slowest_nodes(sort_by='prep')
        self.assertEqual(by_prep[0].node_name, "SlowPrepAsync")

        # By exec time
        by_exec = tracer.get_slowest_nodes(sort_by='exec')
        self.assertEqual(by_exec[0].node_name, "SlowExecAsync")


if __name__ == '__main__':
    unittest.main()
