import unittest
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pocketflow import AsyncNode, AsyncFlow, AsyncBatchFlow, AsyncParallelBatchFlow, AsyncParallelBatchNode

class ConcurrencyTracker:
    """Helper class to track concurrent execution count."""
    def __init__(self):
        self.current_count = 0
        self.max_count = 0
        self.lock = asyncio.Lock()

    async def enter(self):
        async with self.lock:
            self.current_count += 1
            self.max_count = max(self.max_count, self.current_count)

    async def exit(self):
        async with self.lock:
            self.current_count -= 1

class SlowAsyncNode(AsyncNode):
    """Async node that tracks concurrency during execution."""
    def __init__(self, tracker, delay=0.1):
        super().__init__()
        self.tracker = tracker
        self.delay = delay

    async def prep_async(self, shared):
        return self.params.get('batch_id', 0)

    async def exec_async(self, prep_res):
        await self.tracker.enter()
        await asyncio.sleep(self.delay)
        await self.tracker.exit()
        return prep_res

    async def post_async(self, shared, prep_res, exec_res):
        if 'results' not in shared:
            shared['results'] = []
        shared['results'].append(exec_res)
        return "done"


class TestAsyncFlowConcurrencyLimit(unittest.TestCase):
    """Test concurrency limit for AsyncFlow."""

    def test_async_flow_default_no_limit(self):
        """Test that AsyncFlow without concurrency_limit works as before."""
        async def run_test():
            tracker = ConcurrencyTracker()
            node = SlowAsyncNode(tracker, delay=0.01)
            flow = AsyncFlow(start=node)
            shared = {}
            await flow.run_async(shared)
            return shared

        shared = asyncio.run(run_test())
        self.assertEqual(shared['results'], [0])

    def test_async_flow_with_concurrency_limit(self):
        """Test that AsyncFlow accepts concurrency_limit parameter."""
        async def run_test():
            tracker = ConcurrencyTracker()
            node = SlowAsyncNode(tracker, delay=0.01)
            flow = AsyncFlow(start=node, concurrency_limit=2)
            shared = {}
            await flow.run_async(shared)
            return shared

        shared = asyncio.run(run_test())
        self.assertEqual(shared['results'], [0])


class TestAsyncBatchFlowConcurrencyLimit(unittest.TestCase):
    """Test concurrency limit for AsyncBatchFlow (sequential by design)."""

    def test_async_batch_flow_default_no_limit(self):
        """Test that AsyncBatchFlow without concurrency_limit works as before."""
        async def run_test():
            tracker = ConcurrencyTracker()

            class TestBatchFlow(AsyncBatchFlow):
                async def prep_async(self, shared):
                    return [{'batch_id': i} for i in range(5)]

            node = SlowAsyncNode(tracker, delay=0.01)
            flow = TestBatchFlow(start=node)
            shared = {}
            await flow.run_async(shared)
            return shared, tracker.max_count

        shared, max_concurrent = asyncio.run(run_test())
        self.assertEqual(len(shared['results']), 5)
        # AsyncBatchFlow runs sequentially, so max_concurrent should be 1
        self.assertEqual(max_concurrent, 1)

    def test_async_batch_flow_with_concurrency_limit(self):
        """Test that AsyncBatchFlow accepts concurrency_limit (still sequential)."""
        async def run_test():
            tracker = ConcurrencyTracker()

            class TestBatchFlow(AsyncBatchFlow):
                async def prep_async(self, shared):
                    return [{'batch_id': i} for i in range(5)]

            node = SlowAsyncNode(tracker, delay=0.01)
            flow = TestBatchFlow(start=node, concurrency_limit=2)
            shared = {}
            await flow.run_async(shared)
            return shared, tracker.max_count

        shared, max_concurrent = asyncio.run(run_test())
        self.assertEqual(len(shared['results']), 5)
        # AsyncBatchFlow is sequential, so max_concurrent should still be 1
        self.assertEqual(max_concurrent, 1)


class TestAsyncParallelBatchFlowConcurrencyLimit(unittest.TestCase):
    """Test concurrency limit for AsyncParallelBatchFlow."""

    def test_parallel_batch_flow_default_no_limit(self):
        """Test that AsyncParallelBatchFlow without concurrency_limit runs all in parallel."""
        async def run_test():
            tracker = ConcurrencyTracker()

            class TestParallelBatchFlow(AsyncParallelBatchFlow):
                async def prep_async(self, shared):
                    return [{'batch_id': i} for i in range(5)]

            node = SlowAsyncNode(tracker, delay=0.1)
            flow = TestParallelBatchFlow(start=node)
            shared = {}

            start_time = time.time()
            await flow.run_async(shared)
            elapsed = time.time() - start_time

            return shared, tracker.max_count, elapsed

        shared, max_concurrent, elapsed = asyncio.run(run_test())
        self.assertEqual(len(shared['results']), 5)
        # Without limit, all 5 should run concurrently
        self.assertEqual(max_concurrent, 5)
        # Should complete in roughly 0.1s (parallel), not 0.5s (sequential)
        self.assertLess(elapsed, 0.3)

    def test_parallel_batch_flow_with_concurrency_limit_2(self):
        """Test that concurrency_limit=2 limits parallel execution to 2."""
        async def run_test():
            tracker = ConcurrencyTracker()

            class TestParallelBatchFlow(AsyncParallelBatchFlow):
                async def prep_async(self, shared):
                    return [{'batch_id': i} for i in range(6)]

            node = SlowAsyncNode(tracker, delay=0.1)
            flow = TestParallelBatchFlow(start=node, concurrency_limit=2)
            shared = {}

            start_time = time.time()
            await flow.run_async(shared)
            elapsed = time.time() - start_time

            return shared, tracker.max_count, elapsed

        shared, max_concurrent, elapsed = asyncio.run(run_test())
        self.assertEqual(len(shared['results']), 6)
        # With limit=2, max concurrent should be 2
        self.assertLessEqual(max_concurrent, 2)
        # Should take about 0.3s (6 tasks / 2 concurrent = 3 batches * 0.1s)
        self.assertGreater(elapsed, 0.25)
        self.assertLess(elapsed, 0.5)

    def test_parallel_batch_flow_with_concurrency_limit_1(self):
        """Test that concurrency_limit=1 makes execution sequential."""
        async def run_test():
            tracker = ConcurrencyTracker()

            class TestParallelBatchFlow(AsyncParallelBatchFlow):
                async def prep_async(self, shared):
                    return [{'batch_id': i} for i in range(3)]

            node = SlowAsyncNode(tracker, delay=0.1)
            flow = TestParallelBatchFlow(start=node, concurrency_limit=1)
            shared = {}

            start_time = time.time()
            await flow.run_async(shared)
            elapsed = time.time() - start_time

            return shared, tracker.max_count, elapsed

        shared, max_concurrent, elapsed = asyncio.run(run_test())
        self.assertEqual(len(shared['results']), 3)
        # With limit=1, max concurrent should be 1
        self.assertEqual(max_concurrent, 1)
        # Should take about 0.3s (sequential)
        self.assertGreater(elapsed, 0.25)

    def test_parallel_batch_flow_concurrency_limit_greater_than_tasks(self):
        """Test concurrency_limit greater than number of tasks."""
        async def run_test():
            tracker = ConcurrencyTracker()

            class TestParallelBatchFlow(AsyncParallelBatchFlow):
                async def prep_async(self, shared):
                    return [{'batch_id': i} for i in range(3)]

            node = SlowAsyncNode(tracker, delay=0.1)
            flow = TestParallelBatchFlow(start=node, concurrency_limit=10)
            shared = {}

            start_time = time.time()
            await flow.run_async(shared)
            elapsed = time.time() - start_time

            return shared, tracker.max_count, elapsed

        shared, max_concurrent, elapsed = asyncio.run(run_test())
        self.assertEqual(len(shared['results']), 3)
        # With limit=10 but only 3 tasks, max concurrent should be 3
        self.assertEqual(max_concurrent, 3)
        # Should complete quickly (all parallel)
        self.assertLess(elapsed, 0.2)


class TestConcurrencyLimitValidation(unittest.TestCase):
    """Test validation of concurrency_limit parameter."""

    def test_async_flow_rejects_zero_concurrency_limit(self):
        """Test that AsyncFlow raises ValueError for concurrency_limit=0."""
        with self.assertRaises(ValueError) as ctx:
            AsyncFlow(start=None, concurrency_limit=0)
        self.assertIn("concurrency_limit must be at least 1", str(ctx.exception))

    def test_async_flow_rejects_negative_concurrency_limit(self):
        """Test that AsyncFlow raises ValueError for negative concurrency_limit."""
        with self.assertRaises(ValueError) as ctx:
            AsyncFlow(start=None, concurrency_limit=-1)
        self.assertIn("concurrency_limit must be at least 1", str(ctx.exception))

    def test_async_batch_flow_rejects_zero_concurrency_limit(self):
        """Test that AsyncBatchFlow raises ValueError for concurrency_limit=0."""
        with self.assertRaises(ValueError) as ctx:
            AsyncBatchFlow(start=None, concurrency_limit=0)
        self.assertIn("concurrency_limit must be at least 1", str(ctx.exception))

    def test_async_parallel_batch_flow_rejects_zero_concurrency_limit(self):
        """Test that AsyncParallelBatchFlow raises ValueError for concurrency_limit=0."""
        with self.assertRaises(ValueError) as ctx:
            AsyncParallelBatchFlow(start=None, concurrency_limit=0)
        self.assertIn("concurrency_limit must be at least 1", str(ctx.exception))

    def test_async_parallel_batch_node_rejects_zero_concurrency_limit(self):
        """Test that AsyncParallelBatchNode raises ValueError for concurrency_limit=0."""
        with self.assertRaises(ValueError) as ctx:
            AsyncParallelBatchNode(concurrency_limit=0)
        self.assertIn("concurrency_limit must be at least 1", str(ctx.exception))

    def test_async_parallel_batch_node_rejects_negative_concurrency_limit(self):
        """Test that AsyncParallelBatchNode raises ValueError for negative concurrency_limit."""
        with self.assertRaises(ValueError) as ctx:
            AsyncParallelBatchNode(concurrency_limit=-5)
        self.assertIn("concurrency_limit must be at least 1", str(ctx.exception))


class TestAsyncParallelBatchNodeConcurrencyLimit(unittest.TestCase):
    """Test concurrency limit for AsyncParallelBatchNode."""

    def test_parallel_batch_node_default_no_limit(self):
        """Test that AsyncParallelBatchNode without concurrency_limit runs all in parallel."""
        async def run_test():
            tracker = ConcurrencyTracker()

            class TestNode(AsyncParallelBatchNode):
                async def prep_async(self, shared):
                    return shared['items']

                async def exec_async(self, item):
                    await tracker.enter()
                    await asyncio.sleep(0.1)
                    await tracker.exit()
                    return item * 2

                async def post_async(self, shared, prep_res, exec_res):
                    shared['results'] = exec_res
                    return "done"

            node = TestNode()
            shared = {'items': [1, 2, 3, 4, 5]}

            start_time = time.time()
            await node.run_async(shared)
            elapsed = time.time() - start_time

            return shared, tracker.max_count, elapsed

        shared, max_concurrent, elapsed = asyncio.run(run_test())
        self.assertEqual(shared['results'], [2, 4, 6, 8, 10])
        # Without limit, all 5 should run concurrently
        self.assertEqual(max_concurrent, 5)
        # Should complete quickly (parallel)
        self.assertLess(elapsed, 0.2)

    def test_parallel_batch_node_with_concurrency_limit_2(self):
        """Test that AsyncParallelBatchNode with concurrency_limit=2 limits parallel execution."""
        async def run_test():
            tracker = ConcurrencyTracker()

            class TestNode(AsyncParallelBatchNode):
                def __init__(self):
                    super().__init__(concurrency_limit=2)

                async def prep_async(self, shared):
                    return shared['items']

                async def exec_async(self, item):
                    await tracker.enter()
                    await asyncio.sleep(0.1)
                    await tracker.exit()
                    return item * 2

                async def post_async(self, shared, prep_res, exec_res):
                    shared['results'] = exec_res
                    return "done"

            node = TestNode()
            shared = {'items': [1, 2, 3, 4, 5, 6]}

            start_time = time.time()
            await node.run_async(shared)
            elapsed = time.time() - start_time

            return shared, tracker.max_count, elapsed

        shared, max_concurrent, elapsed = asyncio.run(run_test())
        self.assertEqual(shared['results'], [2, 4, 6, 8, 10, 12])
        # With limit=2, max concurrent should be 2
        self.assertLessEqual(max_concurrent, 2)
        # Should take about 0.3s (6 items / 2 concurrent = 3 batches * 0.1s)
        self.assertGreater(elapsed, 0.25)
        self.assertLess(elapsed, 0.5)

    def test_parallel_batch_node_with_concurrency_limit_1(self):
        """Test that AsyncParallelBatchNode with concurrency_limit=1 runs sequentially."""
        async def run_test():
            tracker = ConcurrencyTracker()

            class TestNode(AsyncParallelBatchNode):
                def __init__(self):
                    super().__init__(concurrency_limit=1)

                async def prep_async(self, shared):
                    return shared['items']

                async def exec_async(self, item):
                    await tracker.enter()
                    await asyncio.sleep(0.1)
                    await tracker.exit()
                    return item * 2

                async def post_async(self, shared, prep_res, exec_res):
                    shared['results'] = exec_res
                    return "done"

            node = TestNode()
            shared = {'items': [1, 2, 3]}

            start_time = time.time()
            await node.run_async(shared)
            elapsed = time.time() - start_time

            return shared, tracker.max_count, elapsed

        shared, max_concurrent, elapsed = asyncio.run(run_test())
        self.assertEqual(shared['results'], [2, 4, 6])
        # With limit=1, max concurrent should be 1
        self.assertEqual(max_concurrent, 1)
        # Should take about 0.3s (sequential)
        self.assertGreater(elapsed, 0.25)


if __name__ == '__main__':
    unittest.main()
