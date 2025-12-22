---
layout: default
title: "Flow Structure"
parent: "Core Abstraction"
nav_order: 8
---

# Flow Structure

When building complex flows, it helps to understand the structure **before** running. **FlowStructure** provides static analysis of your flow—giving you a complete picture of how nodes connect, what paths are possible, and potential issues—all without executing any code.

## Why Use FlowStructure?

### The Problem

As flows grow in complexity with branching, loops, and nested sub-flows, it becomes difficult to:
- Understand all possible execution paths
- Verify that transitions are set up correctly
- Find dead-end nodes or unreachable code
- Debug why a flow behaves unexpectedly

### The Solution

`FlowStructure` analyzes your flow graph statically, providing:

| Feature | Benefit |
|---------|---------|
| **Node Discovery** | See all nodes and their types (async, batch, flow) |
| **Transition Mapping** | Understand how nodes connect and what actions trigger each path |
| **Path Enumeration** | List all possible routes through the flow |
| **Loop Detection** | Find cycles and verify they have exit conditions |
| **Validation** | Catch configuration errors before runtime |
| **Visualization** | Generate diagrams for documentation |

### FlowStructure vs FlowTracer

These tools complement each other:

| Aspect | FlowStructure | FlowTracer |
|--------|---------------|------------|
| **When** | Before execution | During/after execution |
| **Shows** | What CAN happen | What DID happen |
| **Use for** | Planning, validation, docs | Debugging, profiling |
| **Input** | Flow definition | Flow execution |

Use **FlowStructure** to understand and validate your flow design.
Use **FlowTracer** to debug actual executions. See [Flow Tracing](tracing.md) for details.

## 1. Basic Usage

```python
from pocketflow import Flow, Node, FlowStructure

# Define your nodes and flow
class ValidateNode(Node):
    def __init__(self):
        super().__init__()
        self.name = "Validate"

    def post(self, shared, prep_res, exec_res):
        if shared.get('valid'):
            return 'valid'
        return 'invalid'

class ProcessNode(Node):
    def __init__(self):
        super().__init__(max_retries=3, wait=1.0)
        self.name = "Process"

class SaveNode(Node):
    def __init__(self):
        super().__init__()
        self.name = "Save"

class ErrorNode(Node):
    def __init__(self):
        super().__init__()
        self.name = "HandleError"

# Build the flow
validate = ValidateNode()
process = ProcessNode()
save = SaveNode()
error = ErrorNode()

validate - "valid" >> process >> save
validate - "invalid" >> error

flow = Flow(start=validate)
flow.name = "DataPipeline"

# Analyze BEFORE running
structure = FlowStructure(flow)
structure.print_structure()
```

### Output

```
============================================================
FLOW STRUCTURE
============================================================
Root: DataPipeline
Total nodes: 5
Total transitions: 4

Entry points: DataPipeline
Exit points: Save, HandleError
Actions used: _start, default, invalid, valid

────────────────────────────────────────
NODES
────────────────────────────────────────

  DataPipeline (Flow) [Flow]
    Transitions:
      --[_start]--> Validate

  Validate (ValidateNode)
    Transitions:
      --[valid]--> Process
      --[invalid]--> HandleError

  Process (ProcessNode)
    Retry: max_retries=3, wait=1.0s
    Transitions:
      --[default]--> Save

  Save (SaveNode)
    (exit point)

  HandleError (ErrorNode)
    (exit point)

────────────────────────────────────────
POSSIBLE PATHS
────────────────────────────────────────
  1. DataPipeline -> Validate -> Process -> Save
  2. DataPipeline -> Validate -> HandleError

============================================================
```

This tells you at a glance:
- **2 possible paths** through the flow
- **2 exit points** (Save and HandleError)
- **Process has retry config** (max_retries=3)
- **All actions used**: valid, invalid, default, _start

## 2. Node Discovery

### Get All Nodes

```python
nodes = structure.get_nodes()
# Returns: Dict[str, NodeInfo]

for name, info in nodes.items():
    flags = []
    if info.is_async: flags.append("async")
    if info.is_batch: flags.append("batch")
    if info.is_flow: flags.append("flow")

    print(f"{name} ({info.node_type}) {flags}")
    if info.retry_config:
        print(f"  Retry: {info.retry_config}")
```

### Get Specific Node

```python
node_info = structure.get_node('Process')
if node_info:
    print(f"Type: {node_info.node_type}")      # "ProcessNode"
    print(f"Is Async: {node_info.is_async}")   # False
    print(f"Is Batch: {node_info.is_batch}")   # False
    print(f"Is Flow: {node_info.is_flow}")     # False
    print(f"Successors: {node_info.successors}")  # {"default": "Save"}
```

### NodeInfo Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Display name (from `node.name` or class name) |
| `node_type` | `str` | Class name (e.g., "AsyncNode", "BatchFlow") |
| `successors` | `Dict[str, str]` | Action → target node name mapping |
| `retry_config` | `Dict[str, Any]` or `None` | Retry settings if `max_retries > 1` |
| `is_flow` | `bool` | True for Flow, AsyncFlow, BatchFlow, etc. |
| `is_async` | `bool` | True for AsyncNode, AsyncFlow, etc. |
| `is_batch` | `bool` | True for BatchNode, BatchFlow, etc. |

## 3. Understanding Transitions

### Get All Transitions

```python
transitions = structure.get_transitions()
# Returns: List[TransitionInfo]

for t in transitions:
    print(f"{t.from_node} --[{t.action}]--> {t.to_node}")

# Output:
# DataPipeline --[_start]--> Validate
# Validate --[valid]--> Process
# Validate --[invalid]--> HandleError
# Process --[default]--> Save
```

### Get Available Actions

```python
actions = structure.get_actions()
print(f"Actions: {sorted(actions)}")
# Actions: ['_start', 'default', 'invalid', 'valid']
```

### Get Successors of a Node

```python
successors = structure.get_successors('Validate')
for action, target in successors.items():
    print(f"On '{action}': go to {target}")

# Output:
# On 'valid': go to Process
# On 'invalid': go to HandleError
```

### Get Predecessors of a Node

```python
predecessors = structure.get_predecessors('Process')
for from_node, action in predecessors:
    print(f"Reached from {from_node} via '{action}'")

# Output:
# Reached from Validate via 'valid'
```

## 4. Entry and Exit Points

```python
# Entry points: nodes with no incoming transitions (except from Flow's _start)
entry_points = structure.get_entry_points()
print(f"Entry points: {entry_points}")  # ['DataPipeline']

# Exit points: nodes with no outgoing transitions
exit_points = structure.get_exit_points()
print(f"Exit points: {exit_points}")  # ['Save', 'HandleError']
```

## 5. Path Analysis

### Get All Possible Paths

```python
paths = structure.get_all_paths()

for i, path in enumerate(paths, 1):
    path_str = ' -> '.join(path.nodes)
    loop_marker = " (LOOP)" if path.has_loop else ""
    print(f"{i}. {path_str}{loop_marker}")

# Output:
# 1. DataPipeline -> Validate -> Process -> Save
# 2. DataPipeline -> Validate -> HandleError
```

### PathInfo Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `nodes` | `List[str]` | Ordered list of node names in the path |
| `actions` | `List[str]` | Actions taken (length = len(nodes) - 1) |
| `has_loop` | `bool` | True if path contains a cycle |

### Filter Paths

```python
# Paths starting from a specific node
paths = structure.get_all_paths(from_node='Validate')

# Paths ending at a specific node
paths = structure.get_all_paths(to_node='HandleError')

# Paths between two specific nodes
paths = structure.get_all_paths(from_node='Validate', to_node='Save')

# Limit depth for complex flows (default: 50)
paths = structure.get_all_paths(max_depth=20)
```

## 6. Loop Detection

Loops are common in agent flows where nodes iterate until a condition is met.

```python
class AgentNode(Node):
    def __init__(self):
        super().__init__()
        self.name = "Agent"

    def post(self, shared, prep_res, exec_res):
        if shared.get('done'):
            return 'finish'
        return 'think_more'

agent = AgentNode()
finish = FinishNode()

agent - "think_more" >> agent  # Self-loop
agent - "finish" >> finish     # Exit

flow = Flow(start=agent)
structure = FlowStructure(flow)

# Check for loops
print(f"Has loops: {structure.has_loops()}")  # True

# Get all loop paths
loops = structure.get_loops()
for loop in loops:
    print(f"Loop: {' -> '.join(loop.nodes)}")
# Loop: Flow -> Agent -> Agent
```

### Why Loop Detection Matters

Loops without exit conditions cause infinite execution. FlowStructure's validation catches these:

```python
# BAD: Infinite loop - no exit!
node_a >> node_b >> node_a

flow = Flow(start=node_a)
structure = FlowStructure(flow)

issues = structure.validate()
# [WARNING] Loop detected (NodeA -> NodeB -> NodeA) with no apparent exit
```

## 7. Validation

Catch configuration errors before runtime:

```python
issues = structure.validate()

for issue in issues:
    print(f"[{issue['severity'].upper()}] {issue['type']}: {issue['message']}")
```

### Issue Types

| Type | Severity | Description |
|------|----------|-------------|
| `missing_start` | error | Flow has no start node |
| `unreachable_node` | warning | Node has no incoming transitions |
| `potential_infinite_loop` | warning | Loop with no exit path detected |

### Validation Examples

```python
# Missing start node
empty_flow = Flow()  # No start!
structure = FlowStructure(empty_flow)
issues = structure.validate()
# [ERROR] missing_start: Flow 'Flow' has no start node defined

# Infinite loop (no exit)
a = ProcessNode()
a.name = "A"
b = ProcessNode()
b.name = "B"
a >> b >> a  # Circular, no exit!

flow = Flow(start=a)
structure = FlowStructure(flow)
issues = structure.validate()
# [WARNING] potential_infinite_loop: Loop detected (A -> B -> A) with no apparent exit
```

### Loop with Valid Exit

Loops WITH exit conditions are fine:

```python
check = CheckNode()
process = ProcessNode()
finish = FinishNode()

check - "continue" >> process >> check  # Loop
check - "done" >> finish                # Exit!

flow = Flow(start=check)
structure = FlowStructure(flow)

issues = structure.validate()
# No infinite loop warning - 'done' action provides exit
```

## 8. Visualization with Mermaid

Generate diagrams for documentation:

```python
mermaid_code = structure.to_mermaid()
print(mermaid_code)
```

Output:
```
graph LR
    DataPipeline[["DataPipeline"]]
    Validate["Validate"]
    Process["Process"]
    Save["Save"]
    HandleError["HandleError"]
    DataPipeline --> Validate
    Validate -->|valid| Process
    Validate -->|invalid| HandleError
    Process --> Save
```

### Node Shapes

| Shape | Meaning |
|-------|---------|
| `[[name]]` | Flow nodes (double brackets) |
| `[/name/]` | Batch nodes (parallelogram) |
| `[name]` | Regular nodes |

### Rendering the Diagram

Paste the output into:
- [Mermaid Live Editor](https://mermaid.live/)
- GitHub markdown (```mermaid code blocks)
- Documentation tools that support Mermaid

## 9. Export to Dictionary

For logging, custom analysis, or integration:

```python
data = structure.to_dict()

import json
print(json.dumps(data, indent=2))
```

Output structure:
```json
{
  "root": "DataPipeline",
  "nodes": {
    "DataPipeline": {
      "name": "DataPipeline",
      "type": "Flow",
      "successors": {"_start": "Validate"},
      "retry_config": null,
      "is_flow": true,
      "is_async": false,
      "is_batch": false
    },
    ...
  },
  "transitions": [
    {"from": "DataPipeline", "to": "Validate", "action": "_start"},
    ...
  ],
  "entry_points": ["DataPipeline"],
  "exit_points": ["Save", "HandleError"],
  "actions": ["_start", "valid", "invalid", "default"],
  "has_loops": false,
  "issues": []
}
```

## 10. Integration with FlowTracer

Compare what COULD happen vs what DID happen:

```python
from pocketflow import FlowStructure, FlowTracer

# Analyze structure BEFORE running
structure = FlowStructure(flow)

# Run with tracer
tracer = FlowTracer()
flow.run({'valid': True}, tracer=tracer)

# Compare
comparison = structure.compare_with_trace(tracer)

print(f"Executed nodes: {comparison['executed_nodes']}")
print(f"Unexecuted nodes: {comparison['unexecuted_nodes']}")
print(f"Node coverage: {comparison['coverage']['nodes']:.1%}")
print(f"Transition coverage: {comparison['coverage']['transitions']:.1%}")
```

Output:
```
Executed nodes: ['Validate', 'Process', 'Save']
Unexecuted nodes: ['HandleError', 'DataPipeline']
Node coverage: 60.0%
Transition coverage: 50.0%
```

### Use Cases for Comparison

| Use Case | What to Check |
|----------|---------------|
| **Test coverage** | Are all paths tested? |
| **Dead code** | Which branches never execute? |
| **Debugging** | Which path was actually taken? |
| **Monitoring** | Track path distribution in production |

## 11. Nested Flows

FlowStructure traverses into nested flows:

```python
# Inner flow
inner_process = ProcessNode()
inner_process.name = "InnerProcess"
inner_flow = Flow(start=inner_process)
inner_flow.name = "SubFlow"

# Outer flow
start = StartNode()
end = EndNode()
start >> inner_flow >> end

outer_flow = Flow(start=start)
outer_flow.name = "MainFlow"

structure = FlowStructure(outer_flow)
nodes = structure.get_nodes()

# All nodes discovered, including nested ones
print("MainFlow" in nodes)     # True
print("SubFlow" in nodes)      # True
print("InnerProcess" in nodes) # True
print("Start" in nodes)        # True
print("End" in nodes)          # True
```

## 12. Async and Batch Detection

FlowStructure correctly identifies node types using `isinstance()`:

```python
from pocketflow import AsyncFlow, AsyncNode, BatchNode, BatchFlow

class MyAsyncNode(AsyncNode):
    pass

class MyBatchNode(BatchNode):
    pass

async_node = MyAsyncNode()
batch_node = MyBatchNode()
async_node >> batch_node

flow = AsyncFlow(start=async_node)
structure = FlowStructure(flow)

# Check types
flow_info = structure.get_node('AsyncFlow')
print(f"is_flow: {flow_info.is_flow}")    # True
print(f"is_async: {flow_info.is_async}")  # True

async_info = structure.get_node('MyAsyncNode')
print(f"is_async: {async_info.is_async}") # True

batch_info = structure.get_node('MyBatchNode')
print(f"is_batch: {batch_info.is_batch}") # True
```

## 13. Practical Examples

### Example 1: Document an API Flow

```python
# Build your flow
api_flow = Flow(start=validate_request)
api_flow.name = "APIRequestHandler"

# Generate documentation
structure = FlowStructure(api_flow)

print("# API Request Handler\n")
print("## Flow Diagram\n")
print("```mermaid")
print(structure.to_mermaid())
print("```\n")

print("## Possible Paths\n")
for i, path in enumerate(structure.get_all_paths(), 1):
    print(f"{i}. {' → '.join(path.nodes)}")

print("\n## Error Handling\n")
for exit_node in structure.get_exit_points():
    info = structure.get_node(exit_node)
    print(f"- **{exit_node}** ({info.node_type})")
```

### Example 2: Validate Before Deployment

```python
def validate_flow_for_deployment(flow):
    """Validate flow configuration before deployment."""
    structure = FlowStructure(flow)
    issues = structure.validate()

    errors = [i for i in issues if i['severity'] == 'error']
    warnings = [i for i in issues if i['severity'] == 'warning']

    if errors:
        print("❌ ERRORS - Cannot deploy:")
        for e in errors:
            print(f"  - {e['message']}")
        return False

    if warnings:
        print("⚠️  WARNINGS:")
        for w in warnings:
            print(f"  - {w['message']}")

    print("✅ Flow validated successfully")
    print(f"   Nodes: {len(structure.get_nodes())}")
    print(f"   Paths: {len(structure.get_all_paths())}")
    print(f"   Exit points: {structure.get_exit_points()}")

    return True

# Use it
if validate_flow_for_deployment(my_flow):
    deploy(my_flow)
```

### Example 3: Test Coverage Analysis

```python
def analyze_test_coverage(flow, test_traces):
    """Analyze which paths are covered by tests."""
    structure = FlowStructure(flow)
    all_paths = structure.get_all_paths()
    all_transitions = set(
        (t.from_node, t.to_node, t.action)
        for t in structure.get_transitions()
    )

    covered_transitions = set()
    for tracer in test_traces:
        for t in tracer.get_transitions():
            covered_transitions.add((t['from'], t['to'], t['action']))

    uncovered = all_transitions - covered_transitions
    coverage = len(covered_transitions) / len(all_transitions) * 100

    print(f"Transition coverage: {coverage:.1f}%")
    if uncovered:
        print("Uncovered transitions:")
        for from_n, to_n, action in uncovered:
            print(f"  {from_n} --[{action}]--> {to_n}")
```

## 14. Performance

FlowStructure analysis is:

- **Fast**: Single graph traversal, results cached
- **Memory-efficient**: Stores metadata only, not node instances
- **Safe**: Read-only, doesn't modify your flow

For very large flows (100+ nodes), limit path enumeration:

```python
# Avoid exponential path explosion
paths = structure.get_all_paths(max_depth=20)
```

## Summary

| Method | Returns | Use For |
|--------|---------|---------|
| `get_nodes()` | `Dict[str, NodeInfo]` | Discover all nodes |
| `get_node(name)` | `NodeInfo` or `None` | Get specific node info |
| `get_transitions()` | `List[TransitionInfo]` | See all connections |
| `get_actions()` | `Set[str]` | List available actions |
| `get_entry_points()` | `List[str]` | Find flow starts |
| `get_exit_points()` | `List[str]` | Find flow ends |
| `get_successors(name)` | `Dict[str, str]` | Node's outgoing transitions |
| `get_predecessors(name)` | `List[Tuple]` | Node's incoming transitions |
| `get_all_paths()` | `List[PathInfo]` | Enumerate all paths |
| `has_loops()` | `bool` | Check for cycles |
| `get_loops()` | `List[PathInfo]` | Get cycle paths |
| `validate()` | `List[Dict]` | Find issues |
| `print_structure()` | None | Human-readable output |
| `to_mermaid()` | `str` | Generate diagram |
| `to_dict()` | `Dict` | Export for processing |
| `compare_with_trace()` | `Dict` | Compare with execution |

---

**See Also:**
- [Flow Tracing](tracing.md) - Debug actual execution
- [Flow Patterns](flow.md) - Flow design patterns
- [Visualization](../utility_function/viz.md) - More visualization options
