import unittest
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pocketflow import Node, AsyncNode, Flow, AsyncFlow, NodeError, FlowTracer, TraceEventType


class TestNodeError(unittest.TestCase):
    """Tests for the NodeError dataclass and its properties."""

    def test_node_error_properties(self):
        """Test that NodeError captures all expected properties."""
        class FailingNode(Node):
            def exec(self, prep_result):
                raise ValueError("Test error message")

            def post(self, shared, prep_result, exec_result):
                return exec_result

        shared = {}
        node = FailingNode(max_retries=2)
        node.run(shared)

        # The post method returns the exec_result which is NodeError
        # We need to capture it differently
        class CaptureNode(Node):
            def exec(self, prep_result):
                raise ValueError("Test error message")

            def post(self, shared, prep_result, exec_result):
                shared['error'] = exec_result
                return None

        shared = {}
        node = CaptureNode(max_retries=2)
        node.run(shared)

        error = shared['error']
        self.assertIsInstance(error, NodeError)
        self.assertIsInstance(error.exception, ValueError)
        self.assertEqual(error.exception_type, "ValueError")
        self.assertEqual(error.message, "Test error message")
        self.assertEqual(error.node_name, "CaptureNode")
        self.assertEqual(error.retry_count, 2)
        self.assertEqual(error.max_retries, 2)
        self.assertIsNotNone(error.traceback_str)
        self.assertIn("ValueError", error.traceback_str)
        self.assertIsInstance(error.timestamp, float)


class TestIsErrorHelper(unittest.TestCase):
    """Tests for the is_error() helper method."""

    def test_is_error_returns_true_for_node_error(self):
        """Test that is_error returns True for NodeError instances."""
        class FailingNode(Node):
            def exec(self, prep_result):
                raise RuntimeError("Failure")

            def post(self, shared, prep_result, exec_result):
                shared['is_error'] = self.is_error(exec_result)
                return None

        shared = {}
        node = FailingNode()
        node.run(shared)

        self.assertTrue(shared['is_error'])

    def test_is_error_returns_false_for_normal_result(self):
        """Test that is_error returns False for non-NodeError values."""
        class SuccessNode(Node):
            def exec(self, prep_result):
                return "success"

            def post(self, shared, prep_result, exec_result):
                shared['is_error'] = self.is_error(exec_result)
                return None

        shared = {}
        node = SuccessNode()
        node.run(shared)

        self.assertFalse(shared['is_error'])

    def test_is_error_returns_false_for_none(self):
        """Test that is_error returns False for None."""
        class NoneResultNode(Node):
            def exec(self, prep_result):
                return None

            def post(self, shared, prep_result, exec_result):
                shared['is_error'] = self.is_error(exec_result)
                return None

        shared = {}
        node = NoneResultNode()
        node.run(shared)

        self.assertFalse(shared['is_error'])


class TestErrorRouting(unittest.TestCase):
    """Tests for error-based routing in flows."""

    def test_automatic_error_routing_in_flow(self):
        """Test that errors are automatically routed to 'error' successor without manual check."""
        class FailingNode(Node):
            def exec(self, prep_result):
                raise ValueError("Intentional failure")

            # No need to check is_error() - orchestrator handles it automatically!
            def post(self, shared, prep_result, exec_result):
                shared['post_called'] = True
                return "success"  # This is ignored when error occurs

        class ErrorHandler(Node):
            def prep(self, shared):
                return shared.get('_error')  # Error is stored in shared['_error']

            def exec(self, prep_result):
                return f"Handled: {prep_result.message}"

            def post(self, shared, prep_result, exec_result):
                shared['handled'] = exec_result
                return None

        class SuccessNode(Node):
            def exec(self, prep_result):
                return "Success path"

            def post(self, shared, prep_result, exec_result):
                shared['success'] = exec_result
                return None

        failing_node = FailingNode()
        error_handler = ErrorHandler()
        success_node = SuccessNode()

        failing_node - "error" >> error_handler
        failing_node - "success" >> success_node

        flow = Flow(start=failing_node)
        shared = {}
        flow.run(shared)

        # Error should be automatically stored in shared['_error']
        self.assertIn('_error', shared)
        self.assertIsInstance(shared['_error'], NodeError)
        self.assertIn('handled', shared)
        self.assertEqual(shared['handled'], "Handled: Intentional failure")
        self.assertNotIn('success', shared)  # Success path not taken

    def test_success_routing_bypasses_error_handler(self):
        """Test that successful execution routes to success path (no auto error routing)."""
        class SucceedingNode(Node):
            def exec(self, prep_result):
                return "worked!"

            def post(self, shared, prep_result, exec_result):
                shared['result'] = exec_result
                return "success"

        class ErrorHandler(Node):
            def exec(self, prep_result):
                return "error handled"

            def post(self, shared, prep_result, exec_result):
                shared['error_handled'] = True
                return None

        class SuccessNode(Node):
            def exec(self, prep_result):
                return "success path"

            def post(self, shared, prep_result, exec_result):
                shared['success_path'] = True
                return None

        succeeding_node = SucceedingNode()
        error_handler = ErrorHandler()
        success_node = SuccessNode()

        succeeding_node - "error" >> error_handler
        succeeding_node - "success" >> success_node

        flow = Flow(start=succeeding_node)
        shared = {}
        flow.run(shared)

        self.assertIn('result', shared)
        self.assertEqual(shared['result'], "worked!")
        self.assertIn('success_path', shared)
        self.assertNotIn('error_handled', shared)
        self.assertNotIn('_error', shared)  # No error occurred

    def test_no_error_successor_continues_normally(self):
        """Test that if no 'error' successor exists, flow continues with post() action."""
        class FailingNode(Node):
            def exec(self, prep_result):
                raise ValueError("Failure")

            def post(self, shared, prep_result, exec_result):
                # Can still manually handle error if no 'error' successor
                if self.is_error(exec_result):
                    shared['manually_handled'] = exec_result.message
                    return "handled"
                return "success"

        class HandledNode(Node):
            def post(self, shared, prep_result, exec_result):
                shared['reached_handled'] = True
                return None

        failing_node = FailingNode()
        handled_node = HandledNode()

        # Only 'handled' successor, no 'error' successor
        failing_node - "handled" >> handled_node

        flow = Flow(start=failing_node)
        shared = {}
        flow.run(shared)

        self.assertIn('manually_handled', shared)
        self.assertEqual(shared['manually_handled'], "Failure")
        self.assertIn('reached_handled', shared)

    def test_manual_error_routing_still_works(self):
        """Test that manual is_error() check in post() still works for custom routing."""
        class FailingNode(Node):
            def exec(self, prep_result):
                raise ValueError("Intentional failure")

            def post(self, shared, prep_result, exec_result):
                if self.is_error(exec_result):
                    shared['error'] = exec_result
                    return "custom_error_action"  # Custom action, not "error"
                return "success"

        class CustomErrorHandler(Node):
            def post(self, shared, prep_result, exec_result):
                shared['custom_handled'] = True
                return None

        failing_node = FailingNode()
        custom_handler = CustomErrorHandler()

        # Custom error action, not "error"
        failing_node - "custom_error_action" >> custom_handler

        flow = Flow(start=failing_node)
        shared = {}
        flow.run(shared)

        self.assertIn('error', shared)
        self.assertIn('custom_handled', shared)


class TestErrorTracing(unittest.TestCase):
    """Tests for NODE_ERROR trace events."""

    def test_node_error_trace_event(self):
        """Test that NODE_ERROR is recorded in traces."""
        class FailingNode(Node):
            def exec(self, prep_result):
                raise RuntimeError("Traced error")

            def post(self, shared, prep_result, exec_result):
                return None

        tracer = FlowTracer()
        shared = {}
        node = FailingNode()
        node.run(shared, tracer=tracer)

        # Find NODE_ERROR event
        error_events = [e for e in tracer.events if e.event_type == TraceEventType.NODE_ERROR]
        self.assertEqual(len(error_events), 1)

        error_event = error_events[0]
        self.assertEqual(error_event.node_name, "FailingNode")
        self.assertIn('error', error_event.data)
        self.assertIn('type', error_event.data)
        self.assertEqual(error_event.data['type'], "RuntimeError")
        self.assertEqual(error_event.data['error'], "Traced error")

    def test_no_node_error_on_success(self):
        """Test that NODE_ERROR is not recorded on success."""
        class SuccessNode(Node):
            def exec(self, prep_result):
                return "success"

            def post(self, shared, prep_result, exec_result):
                return None

        tracer = FlowTracer()
        shared = {}
        node = SuccessNode()
        node.run(shared, tracer=tracer)

        # Ensure no NODE_ERROR event
        error_events = [e for e in tracer.events if e.event_type == TraceEventType.NODE_ERROR]
        self.assertEqual(len(error_events), 0)


class TestAsyncErrorAsState(unittest.TestCase):
    """Tests for async error handling."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_async_automatic_error_routing(self):
        """Test automatic error routing in async flows - no manual check needed."""
        class AsyncFailingNode(AsyncNode):
            async def exec_async(self, prep_result):
                raise ValueError("Async failure")

            # No need to check is_error() - orchestrator handles it automatically!
            async def post_async(self, shared, prep_result, exec_result):
                shared['post_called'] = True
                return "success"  # This is ignored when error occurs

        class AsyncErrorHandler(AsyncNode):
            async def prep_async(self, shared):
                return shared.get('_error')  # Error is stored in shared['_error']

            async def exec_async(self, prep_result):
                return f"Async handled: {prep_result.message}"

            async def post_async(self, shared, prep_result, exec_result):
                shared['handled'] = exec_result
                return None

        async def run_test():
            failing_node = AsyncFailingNode()
            error_handler = AsyncErrorHandler()

            failing_node - "error" >> error_handler

            flow = AsyncFlow(start=failing_node)
            shared = {}
            await flow.run_async(shared)
            return shared

        shared = self.loop.run_until_complete(run_test())

        self.assertIn('_error', shared)
        self.assertIsInstance(shared['_error'], NodeError)
        self.assertIn('handled', shared)
        self.assertEqual(shared['handled'], "Async handled: Async failure")

    def test_async_node_error_properties(self):
        """Test that async NodeError captures all properties correctly."""
        class AsyncCaptureNode(AsyncNode):
            async def exec_async(self, prep_result):
                raise TypeError("Async type error")

            async def post_async(self, shared, prep_result, exec_result):
                shared['error'] = exec_result
                return None

        async def run_test():
            shared = {}
            node = AsyncCaptureNode(max_retries=3)
            await node.run_async(shared)
            return shared

        shared = self.loop.run_until_complete(run_test())

        error = shared['error']
        self.assertIsInstance(error, NodeError)
        self.assertEqual(error.exception_type, "TypeError")
        self.assertEqual(error.message, "Async type error")
        self.assertEqual(error.retry_count, 3)
        self.assertEqual(error.max_retries, 3)

    def test_async_node_error_trace_event(self):
        """Test that NODE_ERROR is recorded in async traces."""
        class AsyncFailingNode(AsyncNode):
            async def exec_async(self, prep_result):
                raise KeyError("Async traced error")

            async def post_async(self, shared, prep_result, exec_result):
                return None

        async def run_test():
            tracer = FlowTracer()
            shared = {}
            node = AsyncFailingNode()
            await node.run_async(shared, tracer=tracer)
            return tracer

        tracer = self.loop.run_until_complete(run_test())

        error_events = [e for e in tracer.events if e.event_type == TraceEventType.NODE_ERROR]
        self.assertEqual(len(error_events), 1)
        self.assertEqual(error_events[0].data['type'], "KeyError")


class TestBackwardCompatibility(unittest.TestCase):
    """Tests to ensure backward compatibility."""

    def test_custom_fallback_still_works(self):
        """Test that custom exec_fallback implementations still work."""
        class CustomFallbackNode(Node):
            def exec(self, prep_result):
                raise ValueError("Will be caught")

            def exec_fallback(self, prep_result, exc):
                return "custom_fallback_value"

            def post(self, shared, prep_result, exec_result):
                shared['result'] = exec_result
                return None

        shared = {}
        node = CustomFallbackNode()
        node.run(shared)

        self.assertEqual(shared['result'], "custom_fallback_value")
        self.assertNotIsInstance(shared['result'], NodeError)

    def test_raise_in_fallback_still_raises(self):
        """Test that raising in exec_fallback still propagates the exception."""
        class RaisingFallbackNode(Node):
            def exec(self, prep_result):
                raise ValueError("Original error")

            def exec_fallback(self, prep_result, exc):
                raise RuntimeError("Re-raised as different type")

            def post(self, shared, prep_result, exec_result):
                return None

        shared = {}
        node = RaisingFallbackNode()

        with self.assertRaises(RuntimeError) as context:
            node.run(shared)

        self.assertEqual(str(context.exception), "Re-raised as different type")


class TestTupleReturnFromRun(unittest.TestCase):
    """Tests for the tuple return value from _run() method.

    The _run() method returns a tuple (action, exec_result) so the orchestrator
    can access both the action from post() and the exec result to check for errors.
    This is safer than storing state on the node instance (avoids race conditions).
    """

    def test_run_returns_tuple_with_action_and_exec_result(self):
        """Test that _run() returns a tuple of (action, exec_result)."""
        class TestNode(Node):
            def exec(self, prep_result):
                return "exec_value"

            def post(self, shared, prep_result, exec_result):
                return "my_action"

        node = TestNode()
        shared = {}
        result = node._run(shared)

        # Should return tuple
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

        action, exec_result = result
        self.assertEqual(action, "my_action")
        self.assertEqual(exec_result, "exec_value")

    def test_run_returns_tuple_with_node_error(self):
        """Test that _run() returns NodeError in the tuple when exec fails."""
        class FailingNode(Node):
            def exec(self, prep_result):
                raise ValueError("Test failure")

            def post(self, shared, prep_result, exec_result):
                return "action_from_post"

        node = FailingNode()
        shared = {}
        result = node._run(shared)

        self.assertIsInstance(result, tuple)
        action, exec_result = result

        self.assertEqual(action, "action_from_post")
        self.assertIsInstance(exec_result, NodeError)
        self.assertEqual(exec_result.exception_type, "ValueError")
        self.assertEqual(exec_result.message, "Test failure")

    def test_public_run_returns_only_action(self):
        """Test that public run() method returns only the action (not tuple)."""
        class TestNode(Node):
            def exec(self, prep_result):
                return "exec_value"

            def post(self, shared, prep_result, exec_result):
                return "my_action"

        node = TestNode()
        shared = {}
        result = node.run(shared)

        # Public API should return just the action
        self.assertEqual(result, "my_action")
        self.assertNotIsInstance(result, tuple)

    def test_custom_run_override_backward_compat(self):
        """Test that custom _run() returning just action still works in flow."""
        class CustomRunNode(Node):
            def _run(self, shared, tracer=None):
                # Old-style _run that returns just action (not tuple)
                shared['custom_run_called'] = True
                return "custom_action"

        class NextNode(Node):
            def post(self, shared, prep_result, exec_result):
                shared['next_node_reached'] = True
                return None

        custom_node = CustomRunNode()
        next_node = NextNode()

        custom_node - "custom_action" >> next_node

        flow = Flow(start=custom_node)
        shared = {}
        flow.run(shared)

        self.assertTrue(shared.get('custom_run_called'))
        self.assertTrue(shared.get('next_node_reached'))

    def test_custom_run_override_no_error_routing(self):
        """Test that custom _run() without tuple doesn't break error routing."""
        class CustomRunNode(Node):
            def _run(self, shared, tracer=None):
                # Custom _run returning just action - no exec_result available
                shared['custom_run_called'] = True
                return "success"

        class ErrorHandler(Node):
            def post(self, shared, prep_result, exec_result):
                shared['error_handler_reached'] = True
                return None

        class SuccessNode(Node):
            def post(self, shared, prep_result, exec_result):
                shared['success_reached'] = True
                return None

        custom_node = CustomRunNode()
        error_handler = ErrorHandler()
        success_node = SuccessNode()

        custom_node - "error" >> error_handler
        custom_node - "success" >> success_node

        flow = Flow(start=custom_node)
        shared = {}
        flow.run(shared)

        # Should follow "success" path since no exec_result means no NodeError
        self.assertTrue(shared.get('custom_run_called'))
        self.assertTrue(shared.get('success_reached'))
        self.assertFalse(shared.get('error_handler_reached', False))


class TestAsyncTupleReturnFromRun(unittest.TestCase):
    """Tests for async tuple return value from _run_async()."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_async_run_returns_tuple(self):
        """Test that _run_async() returns a tuple of (action, exec_result)."""
        class AsyncTestNode(AsyncNode):
            async def exec_async(self, prep_result):
                return "async_exec_value"

            async def post_async(self, shared, prep_result, exec_result):
                return "async_action"

        async def run_test():
            node = AsyncTestNode()
            shared = {}
            result = await node._run_async(shared)
            return result

        result = self.loop.run_until_complete(run_test())

        self.assertIsInstance(result, tuple)
        action, exec_result = result
        self.assertEqual(action, "async_action")
        self.assertEqual(exec_result, "async_exec_value")

    def test_async_run_returns_tuple_with_node_error(self):
        """Test that _run_async() returns NodeError in tuple when exec fails."""
        class AsyncFailingNode(AsyncNode):
            async def exec_async(self, prep_result):
                raise TypeError("Async test failure")

            async def post_async(self, shared, prep_result, exec_result):
                return "action_from_post"

        async def run_test():
            node = AsyncFailingNode()
            shared = {}
            result = await node._run_async(shared)
            return result

        result = self.loop.run_until_complete(run_test())

        self.assertIsInstance(result, tuple)
        action, exec_result = result
        self.assertEqual(action, "action_from_post")
        self.assertIsInstance(exec_result, NodeError)
        self.assertEqual(exec_result.exception_type, "TypeError")

    def test_async_public_run_returns_only_action(self):
        """Test that public run_async() returns only action (not tuple)."""
        class AsyncTestNode(AsyncNode):
            async def exec_async(self, prep_result):
                return "value"

            async def post_async(self, shared, prep_result, exec_result):
                return "the_action"

        async def run_test():
            node = AsyncTestNode()
            shared = {}
            result = await node.run_async(shared)
            return result

        result = self.loop.run_until_complete(run_test())

        self.assertEqual(result, "the_action")
        self.assertNotIsInstance(result, tuple)

    def test_async_custom_run_override_backward_compat(self):
        """Test that custom _run_async() returning just action still works."""
        class CustomAsyncRunNode(AsyncNode):
            async def _run_async(self, shared, tracer=None):
                # Old-style _run_async returning just action
                shared['custom_async_run_called'] = True
                return "custom_async_action"

        class NextAsyncNode(AsyncNode):
            async def post_async(self, shared, prep_result, exec_result):
                shared['next_async_reached'] = True
                return None

        async def run_test():
            custom_node = CustomAsyncRunNode()
            next_node = NextAsyncNode()

            custom_node - "custom_async_action" >> next_node

            flow = AsyncFlow(start=custom_node)
            shared = {}
            await flow.run_async(shared)
            return shared

        shared = self.loop.run_until_complete(run_test())

        self.assertTrue(shared.get('custom_async_run_called'))
        self.assertTrue(shared.get('next_async_reached'))


class TestFlowTupleHandling(unittest.TestCase):
    """Tests for Flow orchestrator handling of tuple return values."""

    def test_flow_orch_unpacks_tuple_correctly(self):
        """Test that Flow._orch correctly unpacks tuple and uses exec_result for error routing."""
        class FailingNode(Node):
            def exec(self, prep_result):
                raise ValueError("Flow test error")

            def post(self, shared, prep_result, exec_result):
                # This action should be overridden by error routing
                return "success"

        class ErrorNode(Node):
            def prep(self, shared):
                return shared.get('_error')

            def exec(self, prep_result):
                return f"Error handled: {prep_result.message}"

            def post(self, shared, prep_result, exec_result):
                shared['error_result'] = exec_result
                return None

        failing = FailingNode()
        error_handler = ErrorNode()

        failing - "error" >> error_handler
        failing - "success" >> Node()  # Dummy success node

        flow = Flow(start=failing)
        shared = {}
        flow.run(shared)

        # Verify error was routed correctly using exec_result from tuple
        self.assertIn('_error', shared)
        self.assertIn('error_result', shared)
        self.assertEqual(shared['error_result'], "Error handled: Flow test error")

    def test_flow_handles_none_exec_result_gracefully(self):
        """Test that Flow handles None exec_result (from custom _run) without crashing."""
        class CustomRunNode(Node):
            def _run(self, shared, tracer=None):
                shared['ran'] = True
                return "next"  # Not a tuple - backward compat

        class NextNode(Node):
            def post(self, shared, prep_result, exec_result):
                shared['next_ran'] = True
                return None

        custom = CustomRunNode()
        next_node = NextNode()

        custom - "next" >> next_node

        flow = Flow(start=custom)
        shared = {}

        # Should not crash even though _run returns non-tuple
        flow.run(shared)

        self.assertTrue(shared['ran'])
        self.assertTrue(shared['next_ran'])


if __name__ == '__main__':
    unittest.main()
