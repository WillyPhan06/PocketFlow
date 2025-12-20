# tests/test_tracing.py
import unittest
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pocketflow import (
    Node, Flow, AsyncNode, AsyncFlow, BatchFlow,
    FlowTracer, TraceEventType, TraceEvent
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


if __name__ == '__main__':
    unittest.main()
