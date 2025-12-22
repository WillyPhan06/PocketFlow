# tests/test_flow_structure.py
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pocketflow import (
    Node, Flow, AsyncNode, AsyncFlow, BatchNode, BatchFlow,
    AsyncBatchFlow, AsyncParallelBatchFlow,
    FlowTracer, FlowStructure, NodeInfo, TransitionInfo, PathInfo
)


# --- Node Definitions for Testing ---

class StartNode(Node):
    def __init__(self):
        super().__init__()
        self.name = "Start"


class ProcessNode(Node):
    def __init__(self):
        super().__init__()
        self.name = "Process"


class EndNode(Node):
    def __init__(self):
        super().__init__()
        self.name = "End"


class CheckNode(Node):
    """Node that branches based on a condition."""
    def __init__(self):
        super().__init__()
        self.name = "Check"

    def post(self, shared, prep_res, exec_res):
        if shared.get('value', 0) > 50:
            return 'high'
        else:
            return 'low'


class RetryNode(Node):
    """Node with retry configuration."""
    def __init__(self):
        super().__init__(max_retries=3, wait=1.0, exponential_backoff=True, max_wait=10.0)
        self.name = "RetryNode"


class LoopNode(Node):
    """Node that can loop back."""
    def __init__(self):
        super().__init__()
        self.name = "Loop"

    def post(self, shared, prep_res, exec_res):
        count = shared.get('count', 0)
        shared['count'] = count + 1
        if count < 3:
            return 'continue'
        return 'done'


class AsyncStartNode(AsyncNode):
    def __init__(self):
        super().__init__()
        self.name = "AsyncStart"


class AsyncEndNode(AsyncNode):
    def __init__(self):
        super().__init__()
        self.name = "AsyncEnd"


class BatchProcessNode(BatchNode):
    def __init__(self):
        super().__init__()
        self.name = "BatchProcess"


# --- Test Cases ---

class TestFlowStructureBasic(unittest.TestCase):
    """Test basic FlowStructure functionality."""

    def test_simple_linear_flow(self):
        """Test analysis of a simple linear flow: Start -> Process -> End"""
        start = StartNode()
        process = ProcessNode()
        end = EndNode()

        start >> process >> end
        flow = Flow(start=start)

        structure = FlowStructure(flow)

        # Check nodes discovered
        nodes = structure.get_nodes()
        self.assertEqual(len(nodes), 4)  # Flow + 3 nodes
        self.assertIn('Start', nodes)
        self.assertIn('Process', nodes)
        self.assertIn('End', nodes)

        # Check transitions (includes _start from Flow to Start)
        transitions = structure.get_transitions()
        self.assertEqual(len(transitions), 3)  # Flow->Start, Start->Process, Process->End

        # Check entry and exit points
        entry = structure.get_entry_points()
        exit_pts = structure.get_exit_points()
        self.assertIn('Flow', entry)  # The flow itself is an entry point
        self.assertIn('End', exit_pts)

    def test_branching_flow(self):
        """Test analysis of a flow with branching."""
        check = CheckNode()
        high_handler = ProcessNode()
        high_handler.name = "HighHandler"
        low_handler = ProcessNode()
        low_handler.name = "LowHandler"

        check - "high" >> high_handler
        check - "low" >> low_handler

        flow = Flow(start=check)

        structure = FlowStructure(flow)

        # Check actions
        actions = structure.get_actions()
        self.assertIn('high', actions)
        self.assertIn('low', actions)

        # Check successors
        check_successors = structure.get_successors('Check')
        self.assertEqual(check_successors['high'], 'HighHandler')
        self.assertEqual(check_successors['low'], 'LowHandler')

        # Check paths - should have 2 paths
        paths = structure.get_all_paths()
        self.assertEqual(len(paths), 2)

    def test_node_with_retry_config(self):
        """Test that retry configuration is captured."""
        retry_node = RetryNode()
        end = EndNode()
        retry_node >> end

        flow = Flow(start=retry_node)
        structure = FlowStructure(flow)

        node_info = structure.get_node('RetryNode')
        self.assertIsNotNone(node_info)
        self.assertIsNotNone(node_info.retry_config)
        self.assertEqual(node_info.retry_config['max_retries'], 3)
        self.assertEqual(node_info.retry_config['wait'], 1.0)
        self.assertTrue(node_info.retry_config['exponential_backoff'])
        self.assertEqual(node_info.retry_config['max_wait'], 10.0)

    def test_node_without_retry_config(self):
        """Test that nodes without retry have no retry_config."""
        start = StartNode()
        flow = Flow(start=start)
        structure = FlowStructure(flow)

        node_info = structure.get_node('Start')
        self.assertIsNone(node_info.retry_config)


class TestFlowStructureLoops(unittest.TestCase):
    """Test loop detection in FlowStructure."""

    def test_simple_loop(self):
        """Test detection of a simple loop."""
        loop_node = LoopNode()
        end = EndNode()

        loop_node - "continue" >> loop_node  # Self-loop
        loop_node - "done" >> end

        flow = Flow(start=loop_node)
        structure = FlowStructure(flow)

        # Should detect the loop
        self.assertTrue(structure.has_loops())
        loops = structure.get_loops()
        self.assertGreater(len(loops), 0)

    def test_loop_with_exit(self):
        """Test that loops with exits don't trigger infinite loop warning."""
        loop_node = LoopNode()
        end = EndNode()

        loop_node - "continue" >> loop_node
        loop_node - "done" >> end

        flow = Flow(start=loop_node)
        structure = FlowStructure(flow)

        # Validate should not report infinite loop since there's an exit
        issues = structure.validate()
        infinite_loop_issues = [i for i in issues if i['type'] == 'potential_infinite_loop']
        self.assertEqual(len(infinite_loop_issues), 0)

    def test_infinite_loop_detection(self):
        """Test detection of potential infinite loops (no exit)."""
        loop1 = ProcessNode()
        loop1.name = "Loop1"
        loop2 = ProcessNode()
        loop2.name = "Loop2"

        loop1 >> loop2 >> loop1  # Circular with no exit

        flow = Flow(start=loop1)
        structure = FlowStructure(flow)

        issues = structure.validate()
        infinite_loop_issues = [i for i in issues if i['type'] == 'potential_infinite_loop']
        self.assertGreater(len(infinite_loop_issues), 0)

    def test_multi_branch_loop_with_exit(self):
        """Test that loops with multiple branches but valid exit don't trigger warning."""
        # Create a loop where multiple branches exist but one has an exit
        check = CheckNode()
        process_high = ProcessNode()
        process_high.name = "ProcessHigh"
        process_low = ProcessNode()
        process_low.name = "ProcessLow"
        end = EndNode()

        # Both branches loop back to check, but check also has exit to end
        check - "high" >> process_high >> check
        check - "low" >> process_low >> check
        check - "done" >> end  # Exit from the loop

        flow = Flow(start=check)
        structure = FlowStructure(flow)

        # Should NOT report infinite loop since 'done' action exits
        issues = structure.validate()
        infinite_loop_issues = [i for i in issues if i['type'] == 'potential_infinite_loop']
        self.assertEqual(len(infinite_loop_issues), 0)

    def test_loop_exit_from_different_node(self):
        """Test that loops with exit from a different node in cycle are valid."""
        node_a = ProcessNode()
        node_a.name = "NodeA"
        node_b = ProcessNode()
        node_b.name = "NodeB"
        node_c = ProcessNode()
        node_c.name = "NodeC"
        end = EndNode()

        # A -> B -> C -> A forms a cycle
        # But B also has exit to End
        node_a >> node_b
        node_b - "continue" >> node_c >> node_a  # Loop back
        node_b - "done" >> end  # Exit from B

        flow = Flow(start=node_a)
        structure = FlowStructure(flow)

        # Should NOT report infinite loop since B has exit to End
        issues = structure.validate()
        infinite_loop_issues = [i for i in issues if i['type'] == 'potential_infinite_loop']
        self.assertEqual(len(infinite_loop_issues), 0)


class TestFlowStructurePaths(unittest.TestCase):
    """Test path analysis in FlowStructure."""

    def test_single_path(self):
        """Test flow with single path."""
        start = StartNode()
        end = EndNode()
        start >> end

        flow = Flow(start=start)
        structure = FlowStructure(flow)

        paths = structure.get_all_paths()
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].nodes, ['Flow', 'Start', 'End'])

    def test_multiple_paths(self):
        """Test flow with multiple paths."""
        check = CheckNode()
        high = ProcessNode()
        high.name = "High"
        low = ProcessNode()
        low.name = "Low"
        end = EndNode()

        check - "high" >> high >> end
        check - "low" >> low >> end

        flow = Flow(start=check)
        structure = FlowStructure(flow)

        paths = structure.get_all_paths()
        # Should have 2 complete paths
        complete_paths = [p for p in paths if not p.has_loop]
        self.assertEqual(len(complete_paths), 2)

    def test_path_from_specific_node(self):
        """Test getting paths from a specific node."""
        start = StartNode()
        middle = ProcessNode()
        end = EndNode()
        start >> middle >> end

        flow = Flow(start=start)
        structure = FlowStructure(flow)

        # Get paths from middle node
        paths = structure.get_all_paths(from_node='Process')
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].nodes, ['Process', 'End'])


class TestFlowStructureValidation(unittest.TestCase):
    """Test flow validation in FlowStructure."""

    def test_missing_start_node(self):
        """Test detection of flow without start node."""
        flow = Flow()  # No start node
        structure = FlowStructure(flow)

        issues = structure.validate()
        missing_start_issues = [i for i in issues if i['type'] == 'missing_start']
        self.assertEqual(len(missing_start_issues), 1)
        self.assertEqual(missing_start_issues[0]['severity'], 'error')

    def test_valid_flow_no_issues(self):
        """Test that valid flows have no errors."""
        start = StartNode()
        end = EndNode()
        start >> end

        flow = Flow(start=start)
        structure = FlowStructure(flow)

        issues = structure.validate()
        errors = [i for i in issues if i['severity'] == 'error']
        self.assertEqual(len(errors), 0)


class TestFlowStructureAsync(unittest.TestCase):
    """Test FlowStructure with async nodes and flows."""

    def test_async_node_detection(self):
        """Test that async nodes are properly identified."""
        async_start = AsyncStartNode()
        async_end = AsyncEndNode()
        async_start >> async_end

        async_flow = AsyncFlow(start=async_start)
        structure = FlowStructure(async_flow)

        # Check async flags
        start_info = structure.get_node('AsyncStart')
        self.assertTrue(start_info.is_async)

        flow_info = structure.get_node('AsyncFlow')
        self.assertTrue(flow_info.is_async)
        self.assertTrue(flow_info.is_flow)


class TestFlowStructureBatch(unittest.TestCase):
    """Test FlowStructure with batch nodes and flows."""

    def test_batch_node_detection(self):
        """Test that batch nodes are properly identified."""
        batch = BatchProcessNode()
        end = EndNode()
        batch >> end

        flow = Flow(start=batch)
        structure = FlowStructure(flow)

        batch_info = structure.get_node('BatchProcess')
        self.assertTrue(batch_info.is_batch)

    def test_batch_flow_detection(self):
        """Test that BatchFlow is properly identified as both flow and batch."""
        start = StartNode()
        batch_flow = BatchFlow(start=start)
        batch_flow.name = "MyBatchFlow"

        structure = FlowStructure(batch_flow)

        flow_info = structure.get_node('MyBatchFlow')
        self.assertTrue(flow_info.is_flow)
        self.assertTrue(flow_info.is_batch)

    def test_async_batch_flow_detection(self):
        """Test that AsyncBatchFlow is identified as flow, async, and batch."""
        async_start = AsyncStartNode()
        async_batch_flow = AsyncBatchFlow(start=async_start)
        async_batch_flow.name = "MyAsyncBatchFlow"

        structure = FlowStructure(async_batch_flow)

        flow_info = structure.get_node('MyAsyncBatchFlow')
        self.assertTrue(flow_info.is_flow)
        self.assertTrue(flow_info.is_async)
        self.assertTrue(flow_info.is_batch)


class TestFlowStructureNestedFlows(unittest.TestCase):
    """Test FlowStructure with nested flows."""

    def test_nested_flow_structure(self):
        """Test analysis of nested flows."""
        # Inner flow
        inner_start = ProcessNode()
        inner_start.name = "InnerStart"
        inner_end = ProcessNode()
        inner_end.name = "InnerEnd"
        inner_start >> inner_end

        inner_flow = Flow(start=inner_start)
        inner_flow.name = "InnerFlow"

        # Outer flow
        outer_start = StartNode()
        outer_end = EndNode()
        outer_start >> inner_flow >> outer_end

        outer_flow = Flow(start=outer_start)
        outer_flow.name = "OuterFlow"

        structure = FlowStructure(outer_flow)

        # Should find all nodes including inner flow's nodes
        nodes = structure.get_nodes()
        self.assertIn('InnerFlow', nodes)
        self.assertIn('InnerStart', nodes)
        self.assertIn('InnerEnd', nodes)

        # Inner flow should be marked as a flow
        inner_info = structure.get_node('InnerFlow')
        self.assertTrue(inner_info.is_flow)


class TestFlowStructureOutput(unittest.TestCase):
    """Test output methods of FlowStructure."""

    def test_to_dict(self):
        """Test serialization to dictionary."""
        start = StartNode()
        end = EndNode()
        start >> end

        flow = Flow(start=start)
        structure = FlowStructure(flow)

        result = structure.to_dict()

        self.assertIn('root', result)
        self.assertIn('nodes', result)
        self.assertIn('transitions', result)
        self.assertIn('entry_points', result)
        self.assertIn('exit_points', result)
        self.assertIn('actions', result)
        self.assertIn('has_loops', result)
        self.assertIn('issues', result)

        self.assertEqual(result['root'], 'Flow')
        self.assertIn('Start', result['nodes'])
        self.assertIn('End', result['nodes'])

    def test_to_mermaid(self):
        """Test Mermaid diagram generation."""
        start = StartNode()
        check = CheckNode()
        high = ProcessNode()
        high.name = "High"
        low = ProcessNode()
        low.name = "Low"

        start >> check
        check - "high" >> high
        check - "low" >> low

        flow = Flow(start=start)
        structure = FlowStructure(flow)

        mermaid = structure.to_mermaid()

        self.assertIn('graph LR', mermaid)
        self.assertIn('Start', mermaid)
        self.assertIn('Check', mermaid)
        self.assertIn('high', mermaid)
        self.assertIn('low', mermaid)

    def test_print_structure(self):
        """Test that print_structure runs without error."""
        start = StartNode()
        end = EndNode()
        start >> end

        flow = Flow(start=start)
        structure = FlowStructure(flow)

        # Should not raise
        import io
        import sys
        captured = io.StringIO()
        sys.stdout = captured
        try:
            structure.print_structure()
        finally:
            sys.stdout = sys.__stdout__

        output = captured.getvalue()
        self.assertIn('FLOW STRUCTURE', output)
        self.assertIn('NODES', output)
        self.assertIn('POSSIBLE PATHS', output)


class TestFlowStructureTracerIntegration(unittest.TestCase):
    """Test integration between FlowStructure and FlowTracer."""

    def test_compare_with_trace(self):
        """Test comparing static structure with execution trace."""
        check = CheckNode()
        high = ProcessNode()
        high.name = "High"
        low = ProcessNode()
        low.name = "Low"

        check - "high" >> high
        check - "low" >> low

        flow = Flow(start=check)
        flow.name = "TestFlow"

        # Analyze structure before running
        structure = FlowStructure(flow)

        # Run with tracer - will take 'low' path since value=0
        tracer = FlowTracer()
        shared = {'value': 0}
        flow.run(shared, tracer=tracer)

        # Compare
        comparison = structure.compare_with_trace(tracer)

        # 'Low' should be executed, 'High' should not
        self.assertIn('Low', comparison['executed_nodes'])
        self.assertIn('High', comparison['unexecuted_nodes'])

        # Coverage should be less than 100% for nodes
        self.assertLess(comparison['coverage']['nodes'], 1.0)

    def test_full_coverage_comparison(self):
        """Test comparison when all paths are executed."""
        start = StartNode()
        end = EndNode()
        start >> end

        flow = Flow(start=start)
        flow.name = "LinearFlow"

        structure = FlowStructure(flow)

        tracer = FlowTracer()
        flow.run({}, tracer=tracer)

        comparison = structure.compare_with_trace(tracer)

        # Check that the internal nodes were executed
        # Note: Flow itself appears in structure but trace records it differently
        self.assertIn('Start', comparison['executed_nodes'])
        self.assertIn('End', comparison['executed_nodes'])

        # Coverage should be high (Flow node might not match exactly due to naming)
        self.assertGreater(comparison['coverage']['nodes'], 0.5)


class TestFlowStructureEdgeCases(unittest.TestCase):
    """Test edge cases for FlowStructure."""

    def test_single_node_flow(self):
        """Test flow with just one node."""
        single = StartNode()
        flow = Flow(start=single)

        structure = FlowStructure(flow)

        nodes = structure.get_nodes()
        self.assertEqual(len(nodes), 2)  # Flow + single node

        paths = structure.get_all_paths()
        self.assertEqual(len(paths), 1)

    def test_node_without_name(self):
        """Test that nodes without explicit names use class name."""
        class UnnamedNode(Node):
            pass

        unnamed = UnnamedNode()
        flow = Flow(start=unnamed)

        structure = FlowStructure(flow)

        nodes = structure.get_nodes()
        self.assertIn('UnnamedNode', nodes)

    def test_multiple_nodes_same_class(self):
        """Test handling of multiple nodes of the same class."""
        node1 = ProcessNode()
        node1.name = "Process1"
        node2 = ProcessNode()
        node2.name = "Process2"
        node3 = ProcessNode()
        node3.name = "Process3"

        node1 >> node2 >> node3

        flow = Flow(start=node1)
        structure = FlowStructure(flow)

        nodes = structure.get_nodes()
        self.assertIn('Process1', nodes)
        self.assertIn('Process2', nodes)
        self.assertIn('Process3', nodes)

    def test_get_predecessors(self):
        """Test getting predecessor nodes."""
        start = StartNode()
        middle = ProcessNode()
        end = EndNode()

        start >> middle >> end

        flow = Flow(start=start)
        structure = FlowStructure(flow)

        predecessors = structure.get_predecessors('Process')
        self.assertEqual(len(predecessors), 1)
        self.assertEqual(predecessors[0], ('Start', 'default'))

        predecessors = structure.get_predecessors('End')
        self.assertEqual(len(predecessors), 1)
        self.assertEqual(predecessors[0], ('Process', 'default'))


class TestFlowStructureComplexAsync(unittest.TestCase):
    """Test FlowStructure with complex async flow patterns."""

    def test_async_flow_with_multiple_async_nodes(self):
        """Test async flow containing multiple async nodes in sequence."""
        async1 = AsyncStartNode()
        async2 = AsyncEndNode()
        async2.name = "AsyncMiddle"
        async3 = AsyncEndNode()

        async1 >> async2 >> async3

        async_flow = AsyncFlow(start=async1)
        async_flow.name = "MultiAsyncFlow"

        structure = FlowStructure(async_flow)

        # All nodes should be detected as async
        self.assertTrue(structure.get_node('AsyncStart').is_async)
        self.assertTrue(structure.get_node('AsyncMiddle').is_async)
        self.assertTrue(structure.get_node('AsyncEnd').is_async)
        self.assertTrue(structure.get_node('MultiAsyncFlow').is_async)

    def test_async_flow_with_branching(self):
        """Test async flow with conditional branches."""
        class AsyncCheckNode(AsyncNode):
            def __init__(self):
                super().__init__()
                self.name = "AsyncCheck"

        check = AsyncCheckNode()
        branch_a = AsyncEndNode()
        branch_a.name = "BranchA"
        branch_b = AsyncEndNode()
        branch_b.name = "BranchB"
        branch_c = AsyncEndNode()
        branch_c.name = "BranchC"

        check - "a" >> branch_a
        check - "b" >> branch_b
        check - "c" >> branch_c

        async_flow = AsyncFlow(start=check)
        structure = FlowStructure(async_flow)

        # Should have 3 paths (one to each branch)
        paths = structure.get_all_paths()
        self.assertEqual(len(paths), 3)

        # All actions should be detected
        actions = structure.get_actions()
        self.assertIn('a', actions)
        self.assertIn('b', actions)
        self.assertIn('c', actions)

    def test_async_parallel_batch_flow(self):
        """Test AsyncParallelBatchFlow detection."""
        async_start = AsyncStartNode()
        parallel_batch_flow = AsyncParallelBatchFlow(start=async_start)
        parallel_batch_flow.name = "ParallelBatchFlow"

        structure = FlowStructure(parallel_batch_flow)

        flow_info = structure.get_node('ParallelBatchFlow')
        self.assertTrue(flow_info.is_flow)
        self.assertTrue(flow_info.is_async)
        self.assertTrue(flow_info.is_batch)

    def test_mixed_sync_async_in_async_flow(self):
        """Test AsyncFlow containing both sync and async nodes."""
        sync_node = StartNode()
        async_node = AsyncEndNode()

        sync_node >> async_node

        # AsyncFlow can contain sync nodes
        async_flow = AsyncFlow(start=sync_node)
        async_flow.name = "MixedFlow"

        structure = FlowStructure(async_flow)

        # Sync node should not be async
        self.assertFalse(structure.get_node('Start').is_async)
        # Async node should be async
        self.assertTrue(structure.get_node('AsyncEnd').is_async)
        # Flow itself is async
        self.assertTrue(structure.get_node('MixedFlow').is_async)


class TestFlowStructureComplexBatch(unittest.TestCase):
    """Test FlowStructure with complex batch flow patterns."""

    def test_batch_flow_with_multiple_batch_nodes(self):
        """Test batch flow containing batch nodes."""
        batch1 = BatchProcessNode()
        batch1.name = "Batch1"
        batch2 = BatchProcessNode()
        batch2.name = "Batch2"

        batch1 >> batch2

        batch_flow = BatchFlow(start=batch1)
        batch_flow.name = "MultiBatchFlow"

        structure = FlowStructure(batch_flow)

        self.assertTrue(structure.get_node('Batch1').is_batch)
        self.assertTrue(structure.get_node('Batch2').is_batch)
        self.assertTrue(structure.get_node('MultiBatchFlow').is_batch)

    def test_batch_node_with_retry_config(self):
        """Test batch node with retry configuration."""
        class RetryBatchNode(BatchNode):
            def __init__(self):
                super().__init__(max_retries=5, wait=2.0, exponential_backoff=True, max_wait=30.0)
                self.name = "RetryBatch"

        retry_batch = RetryBatchNode()
        flow = Flow(start=retry_batch)

        structure = FlowStructure(flow)

        batch_info = structure.get_node('RetryBatch')
        self.assertTrue(batch_info.is_batch)
        self.assertIsNotNone(batch_info.retry_config)
        self.assertEqual(batch_info.retry_config['max_retries'], 5)
        self.assertEqual(batch_info.retry_config['wait'], 2.0)
        self.assertTrue(batch_info.retry_config['exponential_backoff'])
        self.assertEqual(batch_info.retry_config['max_wait'], 30.0)

    def test_async_batch_node_detection(self):
        """Test that AsyncBatchNode is detected as both async and batch."""
        from pocketflow import AsyncBatchNode

        class MyAsyncBatchNode(AsyncBatchNode):
            def __init__(self):
                super().__init__()
                self.name = "MyAsyncBatch"

        async_batch = MyAsyncBatchNode()
        async_flow = AsyncFlow(start=async_batch)

        structure = FlowStructure(async_flow)

        node_info = structure.get_node('MyAsyncBatch')
        self.assertTrue(node_info.is_async)
        self.assertTrue(node_info.is_batch)


class TestFlowStructureDeeplyNested(unittest.TestCase):
    """Test FlowStructure with deeply nested flow structures."""

    def test_three_level_nested_flows(self):
        """Test three levels of nested flows."""
        # Level 3 (innermost)
        inner_node = ProcessNode()
        inner_node.name = "InnerNode"
        inner_flow = Flow(start=inner_node)
        inner_flow.name = "InnerFlow"

        # Level 2
        middle_start = ProcessNode()
        middle_start.name = "MiddleStart"
        middle_start >> inner_flow
        middle_flow = Flow(start=middle_start)
        middle_flow.name = "MiddleFlow"

        # Level 1 (outermost)
        outer_start = StartNode()
        outer_end = EndNode()
        outer_start >> middle_flow >> outer_end
        outer_flow = Flow(start=outer_start)
        outer_flow.name = "OuterFlow"

        structure = FlowStructure(outer_flow)

        # All flows should be detected
        nodes = structure.get_nodes()
        self.assertIn('OuterFlow', nodes)
        self.assertIn('MiddleFlow', nodes)
        self.assertIn('InnerFlow', nodes)

        # All internal nodes should be found
        self.assertIn('InnerNode', nodes)
        self.assertIn('MiddleStart', nodes)
        self.assertIn('Start', nodes)
        self.assertIn('End', nodes)

        # All flows should be marked as flows
        self.assertTrue(nodes['OuterFlow'].is_flow)
        self.assertTrue(nodes['MiddleFlow'].is_flow)
        self.assertTrue(nodes['InnerFlow'].is_flow)

    def test_nested_async_flow_in_sync_flow(self):
        """Test async flow nested inside sync flow."""
        async_inner = AsyncStartNode()
        async_flow = AsyncFlow(start=async_inner)
        async_flow.name = "NestedAsyncFlow"

        sync_start = StartNode()
        sync_end = EndNode()
        sync_start >> async_flow >> sync_end

        outer_flow = Flow(start=sync_start)
        outer_flow.name = "OuterSyncFlow"

        structure = FlowStructure(outer_flow)

        # Outer flow is not async
        self.assertFalse(structure.get_node('OuterSyncFlow').is_async)
        # Inner flow is async
        self.assertTrue(structure.get_node('NestedAsyncFlow').is_async)
        # Inner node is async
        self.assertTrue(structure.get_node('AsyncStart').is_async)

    def test_nested_batch_flow_in_async_flow(self):
        """Test batch flow nested inside async flow."""
        batch_node = BatchProcessNode()
        batch_flow = BatchFlow(start=batch_node)
        batch_flow.name = "NestedBatchFlow"

        async_start = AsyncStartNode()
        async_start >> batch_flow

        outer_flow = AsyncFlow(start=async_start)
        outer_flow.name = "OuterAsyncFlow"

        structure = FlowStructure(outer_flow)

        # Outer is async
        self.assertTrue(structure.get_node('OuterAsyncFlow').is_async)
        # Nested batch flow is batch but not async
        nested_info = structure.get_node('NestedBatchFlow')
        self.assertTrue(nested_info.is_batch)
        self.assertTrue(nested_info.is_flow)

    def test_parallel_nested_flows(self):
        """Test multiple nested flows at the same level (branching to different subflows)."""
        # Two inner flows
        inner_flow_a = Flow(start=ProcessNode())
        inner_flow_a.name = "InnerFlowA"
        inner_flow_a.start_node.name = "ProcessA"

        inner_flow_b = Flow(start=ProcessNode())
        inner_flow_b.name = "InnerFlowB"
        inner_flow_b.start_node.name = "ProcessB"

        # Check node branches to different flows
        check = CheckNode()
        end = EndNode()

        check - "a" >> inner_flow_a >> end
        check - "b" >> inner_flow_b >> end

        outer_flow = Flow(start=check)
        outer_flow.name = "BranchingFlow"

        structure = FlowStructure(outer_flow)

        # Both inner flows should be found
        self.assertIn('InnerFlowA', structure.get_nodes())
        self.assertIn('InnerFlowB', structure.get_nodes())

        # Should have paths through both branches
        paths = structure.get_all_paths()
        # Paths: BranchingFlow -> Check -> InnerFlowA -> ProcessA -> End
        #        BranchingFlow -> Check -> InnerFlowB -> ProcessB -> End
        self.assertGreaterEqual(len(paths), 2)


class TestFlowStructureMixedComplexPatterns(unittest.TestCase):
    """Test FlowStructure with mixed complex patterns combining async, batch, and nesting."""

    def test_async_batch_flow_nested_in_async_flow(self):
        """Test AsyncBatchFlow nested inside AsyncFlow."""
        inner_async = AsyncStartNode()
        inner_async.name = "InnerAsync"
        inner_batch_flow = AsyncBatchFlow(start=inner_async)
        inner_batch_flow.name = "InnerAsyncBatchFlow"

        outer_async = AsyncStartNode()
        outer_async.name = "OuterAsync"
        outer_async >> inner_batch_flow

        outer_flow = AsyncFlow(start=outer_async)
        outer_flow.name = "OuterAsyncFlow"

        structure = FlowStructure(outer_flow)

        outer_info = structure.get_node('OuterAsyncFlow')
        self.assertTrue(outer_info.is_async)
        self.assertTrue(outer_info.is_flow)

        inner_info = structure.get_node('InnerAsyncBatchFlow')
        self.assertTrue(inner_info.is_async)
        self.assertTrue(inner_info.is_batch)
        self.assertTrue(inner_info.is_flow)

    def test_complex_diamond_pattern_with_mixed_nodes(self):
        """Test diamond pattern (A -> B,C -> D) with mixed node types."""
        start = StartNode()
        async_branch = AsyncStartNode()
        async_branch.name = "AsyncBranch"
        batch_branch = BatchProcessNode()
        batch_branch.name = "BatchBranch"
        end = EndNode()

        start - "async" >> async_branch >> end
        start - "batch" >> batch_branch >> end

        flow = Flow(start=start)
        flow.name = "DiamondFlow"

        structure = FlowStructure(flow)

        # Verify node types
        self.assertFalse(structure.get_node('Start').is_async)
        self.assertTrue(structure.get_node('AsyncBranch').is_async)
        self.assertTrue(structure.get_node('BatchBranch').is_batch)

        # Two paths to End
        paths = structure.get_all_paths()
        end_paths = [p for p in paths if p.nodes[-1] == 'End']
        self.assertEqual(len(end_paths), 2)

    def test_loop_with_nested_flow(self):
        """Test loop that contains a nested flow."""
        # Inner flow
        inner_node = ProcessNode()
        inner_node.name = "InnerProcess"
        inner_flow = Flow(start=inner_node)
        inner_flow.name = "InnerFlow"

        # Loop structure
        check = CheckNode()
        check - "continue" >> inner_flow >> check  # Loop back
        check - "done" >> EndNode()

        outer_flow = Flow(start=check)
        outer_flow.name = "LoopWithNestedFlow"

        structure = FlowStructure(outer_flow)

        # Should detect the loop
        self.assertTrue(structure.has_loops())

        # Should NOT report infinite loop (has "done" exit)
        issues = structure.validate()
        infinite_loops = [i for i in issues if i['type'] == 'potential_infinite_loop']
        self.assertEqual(len(infinite_loops), 0)

    def test_multiple_entry_points_with_nested_flows(self):
        """Test structure with multiple entry points due to nested flows."""
        # Main flow
        main_start = StartNode()
        main_end = EndNode()

        # Nested flow that becomes an internal entry point
        nested_start = ProcessNode()
        nested_start.name = "NestedStart"
        nested_flow = Flow(start=nested_start)
        nested_flow.name = "NestedFlow"

        main_start >> nested_flow >> main_end

        outer_flow = Flow(start=main_start)
        outer_flow.name = "MainFlow"

        structure = FlowStructure(outer_flow)

        # Entry points should include MainFlow
        entry_points = structure.get_entry_points()
        self.assertIn('MainFlow', entry_points)

    def test_wide_branching_with_many_exits(self):
        """Test flow with many branches leading to different exits."""
        start = CheckNode()
        start.name = "MultiCheck"

        exits = []
        for i in range(5):
            exit_node = EndNode()
            exit_node.name = f"Exit{i}"
            exits.append(exit_node)
            start - f"branch{i}" >> exit_node

        flow = Flow(start=start)
        flow.name = "WideBranchFlow"

        structure = FlowStructure(flow)

        # Should have 5 exit points
        exit_points = structure.get_exit_points()
        for i in range(5):
            self.assertIn(f'Exit{i}', exit_points)

        # Should have 5 paths
        paths = structure.get_all_paths()
        self.assertEqual(len(paths), 5)

        # Should have 5 branch actions
        actions = structure.get_actions()
        for i in range(5):
            self.assertIn(f'branch{i}', actions)

    def test_chain_of_flows(self):
        """Test a chain of flows connected in sequence."""
        # Create 4 flows in sequence
        flows = []
        for i in range(4):
            inner_node = ProcessNode()
            inner_node.name = f"Process{i}"
            inner_flow = Flow(start=inner_node)
            inner_flow.name = f"Flow{i}"
            flows.append(inner_flow)

        # Chain them: Flow0 -> Flow1 -> Flow2 -> Flow3
        flows[0] >> flows[1] >> flows[2] >> flows[3]

        outer_flow = Flow(start=flows[0])
        outer_flow.name = "ChainedFlows"

        structure = FlowStructure(outer_flow)

        # All flows should be detected
        for i in range(4):
            node_info = structure.get_node(f'Flow{i}')
            self.assertIsNotNone(node_info)
            self.assertTrue(node_info.is_flow)

        # All internal nodes should be found
        for i in range(4):
            self.assertIn(f'Process{i}', structure.get_nodes())


class TestFlowStructureEdgeCasesExtended(unittest.TestCase):
    """Extended edge case tests."""

    def test_node_name_collision_handling(self):
        """Test that nodes with same class name but different instances are handled."""
        class GenericNode(Node):
            pass

        node1 = GenericNode()
        node2 = GenericNode()
        node3 = GenericNode()

        node1 >> node2 >> node3

        flow = Flow(start=node1)
        structure = FlowStructure(flow)

        # Should have all nodes (with disambiguation if needed)
        nodes = structure.get_nodes()
        # At least 4 nodes (Flow + 3 GenericNodes, possibly with _1, _2 suffixes)
        self.assertGreaterEqual(len(nodes), 4)

    def test_self_loop_detection(self):
        """Test detection of self-referencing node."""
        loop_node = LoopNode()
        end = EndNode()

        loop_node - "continue" >> loop_node  # Self-loop
        loop_node - "done" >> end

        flow = Flow(start=loop_node)
        structure = FlowStructure(flow)

        # Should detect loop
        self.assertTrue(structure.has_loops())

        # Should NOT be infinite (has exit)
        issues = structure.validate()
        infinite = [i for i in issues if i['type'] == 'potential_infinite_loop']
        self.assertEqual(len(infinite), 0)

    def test_empty_flow(self):
        """Test flow with no start node."""
        empty_flow = Flow()
        empty_flow.name = "EmptyFlow"

        structure = FlowStructure(empty_flow)

        # Should report missing start
        issues = structure.validate()
        missing_start = [i for i in issues if i['type'] == 'missing_start']
        self.assertEqual(len(missing_start), 1)

    def test_flow_with_only_start_no_successors(self):
        """Test flow where start node has no successors (single node flow)."""
        single = ProcessNode()
        single.name = "Alone"

        flow = Flow(start=single)
        flow.name = "SingleNodeFlow"

        structure = FlowStructure(flow)

        # Should have exactly one exit point (the single node)
        exits = structure.get_exit_points()
        self.assertIn('Alone', exits)

        # Paths should exist
        paths = structure.get_all_paths()
        self.assertGreater(len(paths), 0)

    def test_complex_cycle_with_multiple_entry_exits(self):
        """Test complex cycle with multiple ways in and out."""
        # A -> B -> C -> D -> B (cycle)
        # A also -> E (skip cycle)
        # C also -> F (exit from middle of cycle)
        a = ProcessNode()
        a.name = "A"
        b = ProcessNode()
        b.name = "B"
        c = ProcessNode()
        c.name = "C"
        d = ProcessNode()
        d.name = "D"
        e = EndNode()
        e.name = "E"
        f = EndNode()
        f.name = "F"

        # Build transitions explicitly to avoid precedence issues
        a - "enter_cycle" >> b
        b >> c
        c >> d
        d - "loop" >> b  # Cycle back to B
        a - "skip" >> e  # Skip cycle entirely
        c - "exit" >> f  # Exit from middle of cycle

        flow = Flow(start=a)
        structure = FlowStructure(flow)

        # Should detect the cycle
        self.assertTrue(structure.has_loops())

        # Should NOT report infinite loop (has exits)
        issues = structure.validate()
        infinite = [i for i in issues if i['type'] == 'potential_infinite_loop']
        self.assertEqual(len(infinite), 0)

        # Should find multiple paths
        paths = structure.get_all_paths()
        self.assertGreater(len(paths), 2)

    def test_to_dict_serialization_with_complex_structure(self):
        """Test to_dict produces valid output for complex structure."""
        async_node = AsyncStartNode()
        batch_node = BatchProcessNode()
        async_node >> batch_node

        flow = AsyncFlow(start=async_node)
        flow.name = "SerializationTest"

        structure = FlowStructure(flow)
        data = structure.to_dict()

        # Verify structure
        self.assertIn('root', data)
        self.assertIn('nodes', data)
        self.assertIn('transitions', data)
        self.assertIn('entry_points', data)
        self.assertIn('exit_points', data)
        self.assertIn('actions', data)
        self.assertIn('has_loops', data)
        self.assertIn('issues', data)

        # Verify node info is complete
        for node_name, node_data in data['nodes'].items():
            self.assertIn('name', node_data)
            self.assertIn('type', node_data)
            self.assertIn('successors', node_data)
            self.assertIn('is_flow', node_data)
            self.assertIn('is_async', node_data)
            self.assertIn('is_batch', node_data)

    def test_to_mermaid_with_special_characters(self):
        """Test to_mermaid handles special characters in node names."""
        node = ProcessNode()
        node.name = "Node-With-Dashes And Spaces"

        flow = Flow(start=node)
        flow.name = "Test Flow"

        structure = FlowStructure(flow)
        mermaid = structure.to_mermaid()

        # Should produce valid mermaid (no syntax errors)
        self.assertIn('graph LR', mermaid)
        # Special chars should be escaped
        self.assertNotIn(' And ', mermaid.split('\n')[1] if len(mermaid.split('\n')) > 1 else '')


class TestNodeInfo(unittest.TestCase):
    """Test NodeInfo dataclass."""

    def test_node_info_creation(self):
        """Test creating NodeInfo."""
        info = NodeInfo(
            name="TestNode",
            node_type="Node",
            successors={"default": "NextNode"},
            retry_config={"max_retries": 3},
            is_flow=False,
            is_async=True,
            is_batch=False
        )

        self.assertEqual(info.name, "TestNode")
        self.assertEqual(info.node_type, "Node")
        self.assertEqual(info.successors, {"default": "NextNode"})
        self.assertEqual(info.retry_config, {"max_retries": 3})
        self.assertFalse(info.is_flow)
        self.assertTrue(info.is_async)
        self.assertFalse(info.is_batch)


class TestTransitionInfo(unittest.TestCase):
    """Test TransitionInfo dataclass."""

    def test_transition_info_creation(self):
        """Test creating TransitionInfo."""
        info = TransitionInfo(
            from_node="NodeA",
            to_node="NodeB",
            action="default"
        )

        self.assertEqual(info.from_node, "NodeA")
        self.assertEqual(info.to_node, "NodeB")
        self.assertEqual(info.action, "default")


class TestPathInfo(unittest.TestCase):
    """Test PathInfo dataclass."""

    def test_path_info_creation(self):
        """Test creating PathInfo."""
        info = PathInfo(
            nodes=["A", "B", "C"],
            actions=["default", "default"],
            has_loop=False
        )

        self.assertEqual(info.nodes, ["A", "B", "C"])
        self.assertEqual(info.actions, ["default", "default"])
        self.assertFalse(info.has_loop)

    def test_path_info_with_loop(self):
        """Test PathInfo with loop flag."""
        info = PathInfo(
            nodes=["A", "B", "A"],
            actions=["default", "loop"],
            has_loop=True
        )

        self.assertTrue(info.has_loop)


if __name__ == "__main__":
    unittest.main()
