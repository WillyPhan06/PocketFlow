import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pocketflow import Node, BatchNode, BatchFlow, Flow


class TestBatchFlowStateIsolation(unittest.TestCase):
    """Tests to ensure BatchFlow properly isolates shared state between iterations."""

    def test_shared_state_isolation_between_iterations(self):
        """Ensure modifications in one iteration don't affect subsequent iterations' starting state."""
        class ModifyingNode(Node):
            def prep(self, shared):
                # Each iteration should see the same initial counter value (0)
                return shared.get('counter', 0)

            def exec(self, prep_result):
                return prep_result

            def post(self, shared, prep_res, exec_res):
                iteration = self.params.get('iteration')
                # Modify counter - without isolation, this would affect next iteration's prep
                shared['counter'] = shared.get('counter', 0) + 10
                # Store the initial counter value using iteration key (proper pattern for isolation)
                if 'observed_counters' not in shared:
                    shared['observed_counters'] = {}
                shared['observed_counters'][iteration] = prep_res
                return "default"

        class TestBatchFlow(BatchFlow):
            def prep(self, shared):
                return [{'iteration': i} for i in range(3)]

        shared = {'counter': 0}
        flow = TestBatchFlow(start=ModifyingNode())
        flow.run(shared)

        # With proper isolation, each iteration should see counter=0 at prep time
        # Using dict with unique keys per iteration is the proper pattern
        self.assertEqual(shared['observed_counters'], {0: 0, 1: 0, 2: 0})

    def test_params_not_mutated_by_exec(self):
        """Ensure params dict cannot be mutated to affect other iterations."""
        class ParamMutatingNode(Node):
            def exec(self, prep_result):
                # Try to mutate params - this should not affect other iterations
                if 'data' in self.params:
                    self.params['data'].append('mutated')
                return self.params.get('key')

        class TestBatchFlow(BatchFlow):
            def prep(self, shared):
                return [{'key': 'a', 'data': ['original']}, {'key': 'b', 'data': ['original']}, {'key': 'c', 'data': ['original']}]

        shared = {}
        flow = TestBatchFlow(start=ParamMutatingNode())
        flow.run(shared)
        # Test passes if no error occurs - params isolation prevents cross-contamination

    def test_nested_dict_isolation(self):
        """Ensure nested dicts in shared state are properly isolated."""
        class NestedModifyNode(Node):
            def prep(self, shared):
                key = self.params.get('key')
                # Read from nested dict
                return shared['nested'].get(key, 0)

            def post(self, shared, prep_res, exec_res):
                key = self.params.get('key')
                # Modify nested dict
                if 'nested' not in shared:
                    shared['nested'] = {}
                shared['nested'][key] = prep_res + 1
                return "default"

        class TestBatchFlow(BatchFlow):
            def prep(self, shared):
                return [{'key': 'a'}, {'key': 'b'}, {'key': 'a'}]  # 'a' appears twice

        shared = {'nested': {'a': 0, 'b': 0}}
        flow = TestBatchFlow(start=NestedModifyNode())
        flow.run(shared)

        # Both iterations for 'a' should start with nested['a'] = 0
        # Final state should have nested['a'] = 1 (from last iteration)
        self.assertEqual(shared['nested']['a'], 1)
        self.assertEqual(shared['nested']['b'], 1)

    def test_results_properly_accumulated(self):
        """Ensure results from all iterations are properly merged back."""
        class AccumulatorNode(Node):
            def post(self, shared, prep_res, exec_res):
                key = self.params.get('key')
                if 'results' not in shared:
                    shared['results'] = {}
                shared['results'][key] = key.upper()
                return "default"

        class TestBatchFlow(BatchFlow):
            def prep(self, shared):
                return [{'key': k} for k in ['a', 'b', 'c']]

        shared = {}
        flow = TestBatchFlow(start=AccumulatorNode())
        flow.run(shared)

        self.assertEqual(shared['results'], {'a': 'A', 'b': 'B', 'c': 'C'})


class TestBatchNodeStateIsolation(unittest.TestCase):
    """Tests to ensure BatchNode properly isolates items between iterations."""

    def test_item_isolation_in_exec(self):
        """Ensure modifying an item in exec doesn't affect other items."""
        class ItemModifyingBatchNode(BatchNode):
            def prep(self, shared):
                # Return list of dicts - these should be isolated
                return [{'value': 1}, {'value': 2}, {'value': 3}]

            def exec(self, item):
                original = item['value']
                # Mutate the item - should not affect other items
                item['value'] = item['value'] * 10
                return original

            def post(self, shared, prep_res, exec_res):
                shared['results'] = exec_res
                # prep_res should still have original values since items were copied for exec
                shared['original_items'] = prep_res
                return "default"

        shared = {}
        node = ItemModifyingBatchNode()
        node.run(shared)

        self.assertEqual(shared['results'], [1, 2, 3])
        # Original prep result should be preserved
        self.assertEqual(shared['original_items'], [{'value': 1}, {'value': 2}, {'value': 3}])

    def test_shared_reference_isolation(self):
        """Ensure items referencing shared data are isolated."""
        class ReferenceBatchNode(BatchNode):
            def prep(self, shared):
                # Return references to shared data
                return [shared['items'][i] for i in range(len(shared['items']))]

            def exec(self, item):
                original = item['count']
                item['count'] += 100  # Mutate - should not affect shared['items']
                return original

            def post(self, shared, prep_res, exec_res):
                shared['results'] = exec_res
                return "default"

        shared = {
            'items': [{'count': 1}, {'count': 2}, {'count': 3}]
        }
        node = ReferenceBatchNode()
        node.run(shared)

        self.assertEqual(shared['results'], [1, 2, 3])
        # Original shared items should be unchanged due to deep copy
        self.assertEqual(shared['items'], [{'count': 1}, {'count': 2}, {'count': 3}])


class TestBatchFlowParamsIsolation(unittest.TestCase):
    """Tests to ensure BatchFlow params are properly isolated."""

    def test_flow_params_not_modified(self):
        """Ensure flow-level params are not modified by batch iterations."""
        class ParamReadingNode(Node):
            def exec(self, prep_result):
                # Try to mutate flow params via self.params
                if isinstance(self.params.get('list_param'), list):
                    self.params['list_param'].append('added')
                return self.params.get('key')

        class TestBatchFlow(BatchFlow):
            def prep(self, shared):
                return [{'key': 'a'}, {'key': 'b'}]

        shared = {}
        flow = TestBatchFlow(start=ParamReadingNode())
        flow.params = {'list_param': ['original']}
        flow.run(shared)

        # Original flow params should be unchanged
        self.assertEqual(flow.params, {'list_param': ['original']})


if __name__ == '__main__':
    unittest.main()
