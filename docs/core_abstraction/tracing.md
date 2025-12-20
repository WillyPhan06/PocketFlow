---
layout: default
title: "Tracing"
parent: "Core Abstraction"
nav_order: 7
---

# Flow Tracing

When building complex flows, debugging can be challenging. **Flow Tracing** provides visibility into exactly what happened during a flow execution:

- Which nodes ran and in what order
- What data was passed between nodes
- Why a conditional branch was taken
- When retries occurred and why

Tracing is **optional and lightweight**—it only adds overhead when you explicitly enable it.

## 1. Basic Usage

To enable tracing, create a `FlowTracer` and pass it to `flow.run()`:

```python
from pocketflow import Flow, Node, FlowTracer

# Create your flow as usual
flow = Flow(start=my_node)

# Create a tracer and run the flow with it
tracer = FlowTracer()
flow.run(shared, tracer=tracer)

# View the execution trace
tracer.print_summary()
```

### Example Output

```
============================================================
FLOW EXECUTION TRACE
============================================================
Total duration: 0.0234s
Total events: 12

Execution order: ValidateInput -> ProcessData -> SaveResult

Transitions:
  ValidateInput --[default]--> ProcessData
  ProcessData --[default]--> SaveResult

Detailed timeline:
  [  0.0000s] flow_start      Flow
  [  0.0001s] node_start      ValidateInput
  [  0.0002s] node_prep       ValidateInput
  [  0.0003s] node_exec       ValidateInput
  [  0.0004s] node_post       ValidateInput
  [  0.0005s] node_end        ValidateInput
  [  0.0006s] transition      Flow | {'from_node': 'ValidateInput', 'to_node': 'ProcessData', 'action': 'default'}
  ...
  [  0.0234s] flow_end        Flow
============================================================
```

## 2. Async Flow Tracing

Tracing works the same way with async flows:

```python
from pocketflow import AsyncFlow, AsyncNode, FlowTracer

# Create your async flow
async_flow = AsyncFlow(start=my_async_node)

# Create a tracer and run
tracer = FlowTracer()
await async_flow.run_async(shared, tracer=tracer)

# View the trace
tracer.print_summary()
```

### Async Safety

The tracer uses Python's `contextvars` to ensure **isolation between concurrent flows**. When running multiple async flows in parallel with different tracers, each tracer only captures events from its own flow:

```python
import asyncio

tracer1 = FlowTracer()
tracer2 = FlowTracer()

# Run two flows concurrently - each tracer stays isolated
await asyncio.gather(
    flow1.run_async(shared1, tracer=tracer1),
    flow2.run_async(shared2, tracer=tracer2)
)

# tracer1 only has events from flow1
# tracer2 only has events from flow2
```

## 3. Understanding the Trace

### Event Types

The tracer records these event types:

| Event Type | Description |
|------------|-------------|
| `flow_start` | Flow execution begins |
| `flow_end` | Flow execution ends |
| `node_start` | A node starts executing |
| `node_prep` | Node's `prep()` method completes |
| `node_exec` | Node's `exec()` method completes |
| `node_post` | Node's `post()` method completes |
| `node_end` | Node execution ends |
| `transition` | Flow transitions from one node to another |
| `retry_attempt` | A retry is being attempted |
| `retry_wait` | Waiting before retry (includes wait time and error) |
| `fallback` | Fallback handler is called after all retries exhausted |

### Execution Order

Get a simple list of which nodes ran:

```python
order = tracer.get_execution_order()
# ['ValidateInput', 'ProcessData', 'SaveResult']
```

### Transitions (Branching Decisions)

See exactly which path the flow took:

```python
transitions = tracer.get_transitions()
# [
#   {'from': 'CheckCondition', 'to': 'HandleYes', 'action': 'yes'},
#   {'from': 'HandleYes', 'to': 'Finish', 'action': 'default'}
# ]
```

This is especially useful for debugging conditional branches:

```python
# In your node
class CheckCondition(Node):
    def post(self, shared, prep_res, exec_res):
        if exec_res > 100:
            return "high"    # Goes to HighHandler
        else:
            return "low"     # Goes to LowHandler

# The tracer will show which branch was taken
tracer.print_summary()
# Transitions:
#   CheckCondition --[high]--> HighHandler
```

### Retries

See all retry attempts:

```python
retries = tracer.get_retries()
# [
#   {'node': 'APICallNode', 'retry': 1, 'max_retries': 3},
#   {'node': 'APICallNode', 'retry': 2, 'max_retries': 3},
#   {'node': 'APICallNode', 'retry': 3, 'max_retries': 3}
# ]
```

## 4. Capturing Data

By default, tracing is lightweight and only captures metadata (action names, retry counts, etc.). To capture the actual data passed through `prep()`, `exec()`, and `post()`, enable `capture_data`:

```python
# Enable data capture (may impact performance for large data)
tracer = FlowTracer(capture_data=True)
flow.run(shared, tracer=tracer)

# Now events include the actual data
for event in tracer.events:
    if event.event_type == TraceEventType.NODE_PREP:
        print(f"{event.node_name} prep returned: {event.data}")
```

### Data Truncation

Large data is automatically truncated to prevent memory issues:

```python
# Default max_data_size is 1000 characters
tracer = FlowTracer(capture_data=True, max_data_size=500)
```

## 5. Programmatic Access

### Export to Dictionary

Export the trace for logging or analysis:

```python
trace_dict = tracer.to_dict()
# {
#   'duration': 0.0234,
#   'execution_order': ['Node1', 'Node2', 'Node3'],
#   'transitions': [...],
#   'retries': [...],
#   'events': [...]
# }

# Save to JSON
import json
with open('trace.json', 'w') as f:
    json.dump(trace_dict, f, indent=2)
```

### Access Raw Events

Iterate through all events:

```python
from pocketflow import TraceEventType

for event in tracer.events:
    print(f"[{event.timestamp}] {event.event_type.value}: {event.node_name}")
    if event.data:
        print(f"  Data: {event.data}")
```

### Get Duration

```python
duration = tracer.get_duration()  # Total execution time in seconds
```

### Clear and Reuse

```python
tracer.clear()  # Clear all events to reuse the tracer
```

## 6. Naming Nodes

For clearer traces, give your nodes descriptive names:

```python
class MyNode(Node):
    def __init__(self):
        super().__init__()
        self.name = "ValidateUserInput"  # This appears in the trace
```

If no name is set, the class name is used (e.g., `"MyNode"`).

> **Note**: Node names are cached for performance. If you change `node.name` after the first trace access, the cached name is still used.
{: .warning }

## 7. Nested Flows

When tracing nested flows, all events are captured in the same tracer:

```python
# Inner flow
inner_flow = Flow(start=inner_node1)
inner_flow.name = "InnerFlow"

# Outer flow uses inner flow as a node
outer_flow = Flow(start=inner_flow)
outer_flow.name = "OuterFlow"

tracer = FlowTracer()
outer_flow.run(shared, tracer=tracer)

# The trace shows both flows
tracer.print_summary()
# Execution order: InnerFlow -> InnerNode1 -> InnerNode2 -> OuterNode
```

## 8. Custom Node Overrides

The tracer uses **defensive tracing** at the orchestration level. Even if you override `_run()` in a custom node without calling `super()`, the tracer will still record `node_start` and `node_end` events:

```python
class CustomNode(Node):
    def _run(self, shared, tracer=None):
        # Custom implementation that doesn't call super
        return "custom_action"

# The tracer still captures that this node ran
flow.run(shared, tracer=tracer)
order = tracer.get_execution_order()
# ['CustomNode', ...]  # CustomNode is included
```

## 9. Performance Considerations

Tracing is designed to be lightweight:

- **Zero overhead when disabled**: If you don't pass a tracer, no tracing code runs
- **Minimal overhead when enabled**: Only records event type, node name, and timestamp
- **Optional data capture**: Full data capture is opt-in and can be controlled with `max_data_size`
- **Node name caching**: Names are computed once and cached

For production, you can:
1. Disable tracing entirely (don't pass a tracer)
2. Enable tracing only for specific flows you're debugging
3. Use sampling to trace only a percentage of requests

## 10. Complete Example

Here's a complete example showing tracing with branching and retries:

```python
from pocketflow import Node, Flow, FlowTracer

class FetchData(Node):
    def __init__(self):
        super().__init__(max_retries=3, wait=1)
        self.name = "FetchData"

    def exec(self, prep_res):
        # Simulate occasional failure
        import random
        if random.random() < 0.3:
            raise Exception("Network error")
        return {"value": 42}

    def exec_fallback(self, prep_res, exc):
        return {"value": None, "error": str(exc)}

class CheckValue(Node):
    def __init__(self):
        super().__init__()
        self.name = "CheckValue"

    def prep(self, shared):
        return shared.get("data", {}).get("value")

    def post(self, shared, prep_res, exec_res):
        if prep_res is None:
            return "error"
        elif prep_res > 50:
            return "high"
        else:
            return "low"

class HandleHigh(Node):
    def __init__(self):
        super().__init__()
        self.name = "HandleHigh"

class HandleLow(Node):
    def __init__(self):
        super().__init__()
        self.name = "HandleLow"

class HandleError(Node):
    def __init__(self):
        super().__init__()
        self.name = "HandleError"

# Build the flow
fetch = FetchData()
check = CheckValue()
high = HandleHigh()
low = HandleLow()
error = HandleError()

fetch >> check
check - "high" >> high
check - "low" >> low
check - "error" >> error

flow = Flow(start=fetch)
flow.name = "DataProcessingFlow"

# Run with tracing
shared = {}
tracer = FlowTracer()
flow.run(shared, tracer=tracer)

# Analyze the trace
print("\n=== Trace Analysis ===")
print(f"Execution order: {' -> '.join(tracer.get_execution_order())}")
print(f"Total duration: {tracer.get_duration():.4f}s")

retries = tracer.get_retries()
if retries:
    print(f"Retries occurred: {len(retries)} attempts")

transitions = tracer.get_transitions()
for t in transitions:
    print(f"Transition: {t['from']} --[{t['action']}]--> {t['to']}")

# Full summary
tracer.print_summary()
```

This will output detailed information about the flow execution, including any retries that occurred and which branch was taken based on the data value.
