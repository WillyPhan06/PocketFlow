import unittest
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from pocketflow import Node, AsyncNode


class FailingNode(Node):
    def __init__(self, fail_count, **kwargs):
        super().__init__(**kwargs)
        self.fail_count = fail_count
        self.attempt_count = 0

    def exec(self, prep_res):
        self.attempt_count += 1
        if self.attempt_count <= self.fail_count:
            raise ValueError("Intentional failure")
        return "success"


class AlwaysFailingNode(Node):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.attempt_count = 0

    def exec(self, prep_res):
        self.attempt_count += 1
        raise ValueError("Always fails")

    def exec_fallback(self, prep_res, exc):
        return "fallback"


class AsyncFailingNode(AsyncNode):
    def __init__(self, fail_count, **kwargs):
        super().__init__(**kwargs)
        self.fail_count = fail_count
        self.attempt_count = 0

    async def exec_async(self, prep_res):
        self.attempt_count += 1
        if self.attempt_count <= self.fail_count:
            raise ValueError("Intentional async failure")
        return "success"


class AsyncAlwaysFailingNode(AsyncNode):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.attempt_count = 0

    async def exec_async(self, prep_res):
        self.attempt_count += 1
        raise ValueError("Always fails")

    async def exec_fallback_async(self, prep_res, exc):
        return "fallback"


class TestFixedWaitTime(unittest.TestCase):
    def test_fixed_wait_between_retries(self):
        """Test that fixed wait time is used between retries when exponential_backoff=False"""
        sleep_times = []

        with patch('time.sleep', side_effect=lambda t: sleep_times.append(t)):
            node = AlwaysFailingNode(max_retries=4, wait=1.0, exponential_backoff=False)
            node.run({})

        self.assertEqual(len(sleep_times), 3)
        self.assertEqual(sleep_times, [1.0, 1.0, 1.0])

    def test_no_wait_when_wait_is_zero(self):
        """Test that no sleep is called when wait=0"""
        sleep_times = []

        with patch('time.sleep', side_effect=lambda t: sleep_times.append(t)):
            node = AlwaysFailingNode(max_retries=3, wait=0)
            node.run({})

        self.assertEqual(len(sleep_times), 0)


class TestExponentialBackoff(unittest.TestCase):
    def test_exponential_wait_times(self):
        """Test that wait times increase exponentially: wait, wait*2, wait*4, ..."""
        sleep_times = []

        with patch('time.sleep', side_effect=lambda t: sleep_times.append(t)):
            node = AlwaysFailingNode(max_retries=5, wait=1.0, exponential_backoff=True)
            node.run({})

        self.assertEqual(len(sleep_times), 4)
        self.assertEqual(sleep_times, [1.0, 2.0, 4.0, 8.0])

    def test_exponential_backoff_with_fractional_wait(self):
        """Test exponential backoff with fractional base wait time"""
        sleep_times = []

        with patch('time.sleep', side_effect=lambda t: sleep_times.append(t)):
            node = AlwaysFailingNode(max_retries=4, wait=0.5, exponential_backoff=True)
            node.run({})

        self.assertEqual(len(sleep_times), 3)
        self.assertEqual(sleep_times, [0.5, 1.0, 2.0])


class TestMaxWait(unittest.TestCase):
    def test_max_wait_caps_exponential_backoff(self):
        """Test that max_wait caps exponential backoff wait times"""
        sleep_times = []

        with patch('time.sleep', side_effect=lambda t: sleep_times.append(t)):
            node = AlwaysFailingNode(max_retries=6, wait=1.0, exponential_backoff=True, max_wait=5.0)
            node.run({})

        self.assertEqual(len(sleep_times), 5)
        # Without max_wait: [1, 2, 4, 8, 16]
        # With max_wait=5: [1, 2, 4, 5, 5]
        self.assertEqual(sleep_times, [1.0, 2.0, 4.0, 5.0, 5.0])

    def test_max_wait_with_fixed_wait(self):
        """Test that max_wait also works with fixed wait times"""
        sleep_times = []

        with patch('time.sleep', side_effect=lambda t: sleep_times.append(t)):
            node = AlwaysFailingNode(max_retries=4, wait=10.0, exponential_backoff=False, max_wait=5.0)
            node.run({})

        self.assertEqual(len(sleep_times), 3)
        self.assertEqual(sleep_times, [5.0, 5.0, 5.0])

    def test_max_wait_none_no_cap(self):
        """Test that max_wait=None doesn't cap wait times"""
        sleep_times = []

        with patch('time.sleep', side_effect=lambda t: sleep_times.append(t)):
            node = AlwaysFailingNode(max_retries=5, wait=1.0, exponential_backoff=True, max_wait=None)
            node.run({})

        self.assertEqual(len(sleep_times), 4)
        self.assertEqual(sleep_times, [1.0, 2.0, 4.0, 8.0])


class TestCurRetryInitialization(unittest.TestCase):
    def test_cur_retry_starts_at_zero_each_run(self):
        """Test that cur_retry starts at 0 for each run"""
        node = FailingNode(fail_count=1, max_retries=3, wait=0)

        node.run({})
        self.assertEqual(node.cur_retry, 1)

        # Reset attempt count for second run
        node.attempt_count = 0
        node.fail_count = 1

        node.run({})
        # cur_retry should start at 0 again, not continue from previous value
        self.assertEqual(node.cur_retry, 1)

    def test_cur_retry_reflects_last_attempt(self):
        """Test that cur_retry reflects the index of the last attempt"""
        node = AlwaysFailingNode(max_retries=5, wait=0)
        node.run({})

        # After 5 attempts (indices 0-4), cur_retry should be 4
        self.assertEqual(node.cur_retry, 4)
        self.assertEqual(node.attempt_count, 5)

    def test_cur_retry_reset_after_many_retries(self):
        """Test cur_retry resets properly even after exhausting all retries"""
        node = AlwaysFailingNode(max_retries=10, wait=0)

        # First run exhausts all 10 retries
        node.run({})
        self.assertEqual(node.cur_retry, 9)
        self.assertEqual(node.attempt_count, 10)

        # Second run should start fresh
        node.attempt_count = 0
        node.run({})
        # cur_retry should still be 9 (last attempt index), but importantly
        # the retry loop started from 0, not from the previous value
        self.assertEqual(node.cur_retry, 9)
        self.assertEqual(node.attempt_count, 10)

    def test_cur_retry_zero_on_immediate_success(self):
        """Test cur_retry is 0 when first attempt succeeds"""
        node = FailingNode(fail_count=0, max_retries=5, wait=0)
        node.run({})

        self.assertEqual(node.cur_retry, 0)
        self.assertEqual(node.attempt_count, 1)


class TestAsyncFixedWaitTime(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_async_fixed_wait_between_retries(self):
        """Test that fixed wait time is used between retries for async nodes"""
        sleep_times = []

        async def mock_sleep(t):
            sleep_times.append(t)

        async def run_test():
            with patch('asyncio.sleep', side_effect=mock_sleep):
                node = AsyncAlwaysFailingNode(max_retries=4, wait=1.0, exponential_backoff=False)
                await node.run_async({})

        self.loop.run_until_complete(run_test())

        self.assertEqual(len(sleep_times), 3)
        self.assertEqual(sleep_times, [1.0, 1.0, 1.0])


class TestAsyncExponentialBackoff(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_async_exponential_wait_times(self):
        """Test that async wait times increase exponentially"""
        sleep_times = []

        async def mock_sleep(t):
            sleep_times.append(t)

        async def run_test():
            with patch('asyncio.sleep', side_effect=mock_sleep):
                node = AsyncAlwaysFailingNode(max_retries=5, wait=1.0, exponential_backoff=True)
                await node.run_async({})

        self.loop.run_until_complete(run_test())

        self.assertEqual(len(sleep_times), 4)
        self.assertEqual(sleep_times, [1.0, 2.0, 4.0, 8.0])


class TestAsyncMaxWait(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_async_max_wait_caps_exponential_backoff(self):
        """Test that max_wait caps exponential backoff for async nodes"""
        sleep_times = []

        async def mock_sleep(t):
            sleep_times.append(t)

        async def run_test():
            with patch('asyncio.sleep', side_effect=mock_sleep):
                node = AsyncAlwaysFailingNode(max_retries=6, wait=1.0, exponential_backoff=True, max_wait=5.0)
                await node.run_async({})

        self.loop.run_until_complete(run_test())

        self.assertEqual(len(sleep_times), 5)
        self.assertEqual(sleep_times, [1.0, 2.0, 4.0, 5.0, 5.0])


class TestAsyncCurRetryInitialization(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_async_cur_retry_starts_at_zero_each_run(self):
        """Test that cur_retry starts at 0 for each async run"""
        async def run_test():
            node = AsyncFailingNode(fail_count=1, max_retries=3, wait=0)

            await node.run_async({})
            first_cur_retry = node.cur_retry

            # Reset attempt count for second run
            node.attempt_count = 0
            node.fail_count = 1

            await node.run_async({})
            second_cur_retry = node.cur_retry

            return first_cur_retry, second_cur_retry

        first, second = self.loop.run_until_complete(run_test())

        self.assertEqual(first, 1)
        self.assertEqual(second, 1)


class TestBackwardsCompatibility(unittest.TestCase):
    def test_default_parameters_fixed_wait(self):
        """Test that default parameters maintain backwards compatibility"""
        node = Node(max_retries=3, wait=1.0)

        self.assertEqual(node.max_retries, 3)
        self.assertEqual(node.wait, 1.0)
        self.assertEqual(node.exponential_backoff, False)
        self.assertIsNone(node.max_wait)

    def test_existing_code_without_new_params_works(self):
        """Test that existing code patterns continue to work"""
        sleep_times = []

        with patch('time.sleep', side_effect=lambda t: sleep_times.append(t)):
            # Old-style initialization without new parameters
            node = AlwaysFailingNode(max_retries=3, wait=0.5)
            node.run({})

        # Should use fixed wait times
        self.assertEqual(len(sleep_times), 2)
        self.assertEqual(sleep_times, [0.5, 0.5])


class TestGetWaitTimeHelper(unittest.TestCase):
    def test_get_wait_time_fixed(self):
        """Test _get_wait_time returns fixed wait time"""
        node = Node(max_retries=5, wait=2.0, exponential_backoff=False)

        self.assertEqual(node._get_wait_time(0), 2.0)
        self.assertEqual(node._get_wait_time(1), 2.0)
        self.assertEqual(node._get_wait_time(5), 2.0)

    def test_get_wait_time_exponential(self):
        """Test _get_wait_time returns exponential wait time"""
        node = Node(max_retries=5, wait=1.0, exponential_backoff=True)

        self.assertEqual(node._get_wait_time(0), 1.0)   # 1 * 2^0 = 1
        self.assertEqual(node._get_wait_time(1), 2.0)   # 1 * 2^1 = 2
        self.assertEqual(node._get_wait_time(2), 4.0)   # 1 * 2^2 = 4
        self.assertEqual(node._get_wait_time(3), 8.0)   # 1 * 2^3 = 8

    def test_get_wait_time_with_max_wait(self):
        """Test _get_wait_time respects max_wait"""
        node = Node(max_retries=10, wait=1.0, exponential_backoff=True, max_wait=5.0)

        self.assertEqual(node._get_wait_time(0), 1.0)
        self.assertEqual(node._get_wait_time(1), 2.0)
        self.assertEqual(node._get_wait_time(2), 4.0)
        self.assertEqual(node._get_wait_time(3), 5.0)  # Would be 8, capped to 5
        self.assertEqual(node._get_wait_time(10), 5.0) # Would be 1024, capped to 5

    def test_get_wait_time_zero_wait(self):
        """Test _get_wait_time returns 0 when wait=0"""
        node = Node(max_retries=5, wait=0, exponential_backoff=True)

        self.assertEqual(node._get_wait_time(0), 0)
        self.assertEqual(node._get_wait_time(5), 0)


class TestSyncAsyncConsistency(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_sync_async_same_wait_times_exponential(self):
        """Test that sync and async nodes produce identical wait times"""
        sync_sleep_times = []
        async_sleep_times = []

        # Run sync node
        with patch('time.sleep', side_effect=lambda t: sync_sleep_times.append(t)):
            sync_node = AlwaysFailingNode(max_retries=5, wait=1.0, exponential_backoff=True, max_wait=10.0)
            sync_node.run({})

        # Run async node
        async def mock_sleep(t):
            async_sleep_times.append(t)

        async def run_async():
            with patch('asyncio.sleep', side_effect=mock_sleep):
                async_node = AsyncAlwaysFailingNode(max_retries=5, wait=1.0, exponential_backoff=True, max_wait=10.0)
                await async_node.run_async({})

        self.loop.run_until_complete(run_async())

        # Both should have identical wait times
        self.assertEqual(sync_sleep_times, async_sleep_times)
        self.assertEqual(sync_sleep_times, [1.0, 2.0, 4.0, 8.0])

    def test_sync_async_same_wait_times_fixed(self):
        """Test that sync and async nodes produce identical fixed wait times"""
        sync_sleep_times = []
        async_sleep_times = []

        # Run sync node
        with patch('time.sleep', side_effect=lambda t: sync_sleep_times.append(t)):
            sync_node = AlwaysFailingNode(max_retries=4, wait=2.5, exponential_backoff=False)
            sync_node.run({})

        # Run async node
        async def mock_sleep(t):
            async_sleep_times.append(t)

        async def run_async():
            with patch('asyncio.sleep', side_effect=mock_sleep):
                async_node = AsyncAlwaysFailingNode(max_retries=4, wait=2.5, exponential_backoff=False)
                await async_node.run_async({})

        self.loop.run_until_complete(run_async())

        # Both should have identical wait times
        self.assertEqual(sync_sleep_times, async_sleep_times)
        self.assertEqual(sync_sleep_times, [2.5, 2.5, 2.5])

    def test_sync_async_use_same_helper(self):
        """Test that both sync and async nodes use _get_wait_time from Node class"""
        sync_node = AlwaysFailingNode(max_retries=5, wait=1.0, exponential_backoff=True, max_wait=3.0)
        async_node = AsyncAlwaysFailingNode(max_retries=5, wait=1.0, exponential_backoff=True, max_wait=3.0)

        # Both should have the same _get_wait_time method inherited from Node
        for i in range(5):
            self.assertEqual(sync_node._get_wait_time(i), async_node._get_wait_time(i))


if __name__ == '__main__':
    unittest.main()
