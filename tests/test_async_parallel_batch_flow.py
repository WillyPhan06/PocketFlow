import unittest
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pocketflow import AsyncNode, AsyncParallelBatchNode, AsyncParallelBatchFlow, AsyncFlow

class AsyncParallelNumberProcessor(AsyncParallelBatchNode):
    def __init__(self, delay=0.1):
        super().__init__()
        self.delay = delay

    async def prep_async(self, shared_storage):
        batch = shared_storage['batches'][self.params['batch_id']]
        return batch

    async def exec_async(self, number):
        await asyncio.sleep(self.delay)  # Simulate async processing
        return number * 2

    async def post_async(self, shared_storage, prep_result, exec_result):
        if 'processed_numbers' not in shared_storage:
            shared_storage['processed_numbers'] = {}
        shared_storage['processed_numbers'][self.params['batch_id']] = exec_result
        return "done"

class AsyncAggregatorNode(AsyncNode):
    async def prep_async(self, shared_storage):
        # Combine all batch results in order
        all_results = []
        processed = shared_storage.get('processed_numbers', {})
        for i in range(len(processed)):
            all_results.extend(processed[i])
        return all_results

    async def exec_async(self, prep_result):
        await asyncio.sleep(0.01)
        return sum(prep_result)

    async def post_async(self, shared_storage, prep_result, exec_result):
        shared_storage['total'] = exec_result
        return "aggregated"

class TestAsyncParallelBatchFlow(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_parallel_batch_flow(self):
        """
        Test basic parallel batch processing flow with batch IDs.
        With proper state isolation, aggregation should happen AFTER the parallel batch flow completes,
        not within the flow itself. This test verifies that results are properly merged.
        """
        class TestParallelBatchFlow(AsyncParallelBatchFlow):
            async def prep_async(self, shared_storage):
                return [{'batch_id': i} for i in range(len(shared_storage['batches']))]

        shared_storage = {
            'batches': [
                [1, 2, 3],  # batch_id: 0
                [4, 5, 6],  # batch_id: 1
                [7, 8, 9]   # batch_id: 2
            ]
        }

        processor = AsyncParallelNumberProcessor(delay=0.1)
        # Note: Aggregator runs AFTER the parallel batch flow, not within each iteration
        flow = TestParallelBatchFlow(start=processor)

        start_time = self.loop.time()
        self.loop.run_until_complete(flow.run_async(shared_storage))
        execution_time = self.loop.time() - start_time

        # Verify each batch was processed correctly (results merged from all parallel tasks)
        expected_batch_results = {
            0: [2, 4, 6],    # [1,2,3] * 2
            1: [8, 10, 12],  # [4,5,6] * 2
            2: [14, 16, 18]  # [7,8,9] * 2
        }
        self.assertEqual(shared_storage['processed_numbers'], expected_batch_results)

        # Verify parallel execution (should be faster than sequential)
        self.assertLess(execution_time, 0.2)

        # Now run aggregator separately to compute total
        aggregator = AsyncAggregatorNode()
        self.loop.run_until_complete(aggregator.run_async(shared_storage))

        # Verify total
        expected_total = sum(num * 2 for batch in shared_storage['batches'] for num in batch)
        self.assertEqual(shared_storage['total'], expected_total)

    def test_error_handling(self):
        """
        Test error handling in parallel batch flow
        """
        class ErrorProcessor(AsyncParallelNumberProcessor):
            async def exec_async(self, item):
                if item == 2:
                    raise ValueError(f"Error processing item {item}")
                return item

        class ErrorBatchFlow(AsyncParallelBatchFlow):
            async def prep_async(self, shared_storage):
                return [{'batch_id': i} for i in range(len(shared_storage['batches']))]

        shared_storage = {
            'batches': [
                [1, 2, 3],  # Contains error-triggering value
                [4, 5, 6]
            ]
        }

        processor = ErrorProcessor()
        flow = ErrorBatchFlow(start=processor)
        
        with self.assertRaises(ValueError):
            self.loop.run_until_complete(flow.run_async(shared_storage))

    def test_multiple_batch_sizes(self):
        """
        Test parallel batch flow with varying batch sizes.
        With proper state isolation, aggregation should happen AFTER the parallel batch flow completes.
        """
        class VaryingBatchFlow(AsyncParallelBatchFlow):
            async def prep_async(self, shared_storage):
                return [{'batch_id': i} for i in range(len(shared_storage['batches']))]

        shared_storage = {
            'batches': [
                [1],           # batch_id: 0
                [2, 3, 4],    # batch_id: 1
                [5, 6],       # batch_id: 2
                [7, 8, 9, 10] # batch_id: 3
            ]
        }

        processor = AsyncParallelNumberProcessor(delay=0.05)
        # Note: Aggregator runs AFTER the parallel batch flow, not within each iteration
        flow = VaryingBatchFlow(start=processor)

        self.loop.run_until_complete(flow.run_async(shared_storage))

        # Verify each batch was processed correctly (results merged from all parallel tasks)
        expected_batch_results = {
            0: [2],                 # [1] * 2
            1: [4, 6, 8],          # [2,3,4] * 2
            2: [10, 12],           # [5,6] * 2
            3: [14, 16, 18, 20]    # [7,8,9,10] * 2
        }
        self.assertEqual(shared_storage['processed_numbers'], expected_batch_results)

        # Now run aggregator separately to compute total
        aggregator = AsyncAggregatorNode()
        self.loop.run_until_complete(aggregator.run_async(shared_storage))

        # Verify total
        expected_total = sum(num * 2 for batch in shared_storage['batches'] for num in batch)
        self.assertEqual(shared_storage['total'], expected_total)

    def test_shared_list_append_order(self):
        """
        Test that when multiple concurrent tasks all append to the same shared list,
        the results are merged in the correct index order (deterministic order based on
        batch index, not task completion order).

        This is critical because parallel tasks may complete in any order, but the merge
        should always produce results ordered by batch index to ensure deterministic behavior.
        """
        class ListAppendNode(AsyncNode):
            async def prep_async(self, shared):
                return self.params.get('batch_id')

            async def exec_async(self, batch_id):
                # Use varying delays to make tasks complete in different orders
                # Higher batch_id = shorter delay, so they complete in REVERSE order
                delay = 0.1 - (batch_id * 0.015)
                await asyncio.sleep(max(delay, 0.01))
                return f"result_{batch_id}"

            async def post_async(self, shared, prep_res, exec_res):
                # Each task appends to the same shared list
                if 'results' not in shared:
                    shared['results'] = []
                shared['results'].append(exec_res)
                return "done"

        class ListAppendBatchFlow(AsyncParallelBatchFlow):
            async def prep_async(self, shared):
                # Create 6 batch params to have enough tasks for race conditions
                return [{'batch_id': i} for i in range(6)]

        shared_storage = {}
        node = ListAppendNode()
        flow = ListAppendBatchFlow(start=node)

        self.loop.run_until_complete(flow.run_async(shared_storage))

        # Even though tasks complete in reverse order (batch_id 5 finishes first),
        # the merge should produce results in batch index order (0, 1, 2, 3, 4, 5)
        expected_results = ['result_0', 'result_1', 'result_2', 'result_3', 'result_4', 'result_5']
        self.assertEqual(shared_storage['results'], expected_results)

    def test_shared_list_append_order_with_concurrency_limit(self):
        """
        Test that list append order is correct even when using concurrency_limit.
        With concurrency_limit, tasks run in batches, but final merge order should
        still be deterministic based on batch index.
        """
        class ListAppendNode(AsyncNode):
            async def prep_async(self, shared):
                return self.params.get('batch_id')

            async def exec_async(self, batch_id):
                # Varying delays to shuffle completion order within each concurrent batch
                delay = 0.05 if batch_id % 2 == 0 else 0.02
                await asyncio.sleep(delay)
                return f"item_{batch_id}"

            async def post_async(self, shared, prep_res, exec_res):
                if 'items' not in shared:
                    shared['items'] = []
                shared['items'].append(exec_res)
                return "done"

        class LimitedBatchFlow(AsyncParallelBatchFlow):
            async def prep_async(self, shared):
                return [{'batch_id': i} for i in range(8)]

        shared_storage = {}
        node = ListAppendNode()
        # Limit to 2 concurrent tasks at a time
        flow = LimitedBatchFlow(start=node, concurrency_limit=2)

        self.loop.run_until_complete(flow.run_async(shared_storage))

        # Results should be in batch index order regardless of completion order
        expected_items = [f'item_{i}' for i in range(8)]
        self.assertEqual(shared_storage['items'], expected_items)

if __name__ == '__main__':
    unittest.main()