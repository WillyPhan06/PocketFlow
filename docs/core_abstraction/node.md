---
layout: default
title: "Node"
parent: "Core Abstraction"
nav_order: 1
---

# Node

A **Node** is the smallest building block. Each Node has 3 steps `prep->exec->post`:

<div align="center">
  <img src="https://github.com/the-pocket/.github/raw/main/assets/node.png?raw=true" width="400"/>
</div>

1. `prep(shared)`
   - **Read and preprocess data** from `shared` store. 
   - Examples: *query DB, read files, or serialize data into a string*.
   - Return `prep_res`, which is used by `exec()` and `post()`.

2. `exec(prep_res)`
   - **Execute compute logic**, with optional retries and error handling (below).
   - Examples: *(mostly) LLM calls, remote APIs, tool use*.
   - ⚠️ This shall be only for compute and **NOT** access `shared`.
   - ⚠️ If retries enabled, ensure idempotent implementation.
   - ⚠️ Defer exception handling to the Node's built-in retry mechanism.
   - Return `exec_res`, which is passed to `post()`.

3. `post(shared, prep_res, exec_res)`
   - **Postprocess and write data** back to `shared`.
   - Examples: *update DB, change states, log results*.
   - **Decide the next action** by returning a *string* (`action = "default"` if *None*).

> **Why 3 steps?** To enforce the principle of *separation of concerns*. The data storage and data processing are operated separately.
>
> All steps are *optional*. E.g., you can only implement `prep` and `post` if you just need to process data.
{: .note }

### Fault Tolerance & Retries

You can **retry** `exec()` if it raises an exception via two parameters when define the Node:

- `max_retries` (int): Max times to run `exec()`. The default is `1` (**no** retry).
- `wait` (int): The time to wait (in **seconds**) before next retry. By default, `wait=0` (no waiting). 
`wait` is helpful when you encounter rate-limits or quota errors from your LLM provider and need to back off.

```python 
my_node = SummarizeFile(max_retries=3, wait=10)
```

When an exception occurs in `exec()`, the Node automatically retries until:

- It either succeeds, or
- The Node has retried `max_retries - 1` times already and fails on the last attempt.

You can get the current retry times (0-based) from `self.cur_retry`.

```python 
class RetryNode(Node):
    def exec(self, prep_res):
        print(f"Retry {self.cur_retry} times")
        raise Exception("Failed")
```

### Error as State (Automatic Error Routing)

By default, when `exec()` fails after all retries, the Node returns a `NodeError` object instead of crashing. **The Flow orchestrator automatically detects this and routes to an `"error"` successor if one exists** - no manual checking required!

#### Why Error as State?

Traditional exception handling crashes the entire flow when a node fails, leaving developers with:
- **No graceful recovery path** - the flow just dies
- **Limited debugging info** - stack traces without context
- **No flow control** - can't route to different handlers based on error types

By treating errors as state (`NodeError`), the flow can:
- **Continue executing** through error-handling nodes
- **Preserve rich error context** for debugging and logging
- **Route intelligently** to different handlers based on error types
- **Keep the workflow smooth** without unexpected crashes

```python
from pocketflow import Node, Flow, NodeError

class APINode(Node):
    def exec(self, prep_res):
        return call_external_api()  # Might fail

    def post(self, shared, prep_res, exec_res):
        # No need to check for errors - orchestrator handles it!
        shared['result'] = exec_res
        return "success"

class ErrorHandler(Node):
    def prep(self, shared):
        return shared.get('_error')  # Error auto-stored in shared['_error']

    def exec(self, prep_res):
        # Access rich error info
        print(f"Error type: {prep_res.exception_type}")
        print(f"Message: {prep_res.message}")
        print(f"Retries: {prep_res.retry_count}/{prep_res.max_retries}")
        print(f"Traceback:\n{prep_res.traceback_str}")
        return "handled"

# Flow setup with error routing
api_node = APINode(max_retries=3)
error_handler = ErrorHandler()
success_node = SuccessNode()

api_node - "success" >> success_node
api_node - "error" >> error_handler  # Automatically routes here on error!

flow = Flow(start=api_node)
flow.run(shared)
```

**How it works:**
1. When `exec()` fails after retries, `exec_fallback()` returns a `NodeError`
2. The Flow orchestrator checks if the node has an `"error"` successor
3. If yes, it stores the error in `shared["_error"]` and routes to the error handler
4. If no `"error"` successor exists, the `post()` action is used (allowing manual handling)

The `NodeError` object contains:
- `exception`: The original exception object
- `exception_type`: String name of the exception (e.g., "ValueError")
- `message`: The error message
- `node_name`: Name of the node that failed
- `retry_count`: How many attempts were made
- `max_retries`: Maximum retries configured
- `traceback_str`: Full traceback for debugging
- `timestamp`: When the error occurred

#### Implementation Design: Tuple Return Pattern

The Flow orchestrator receives both the action and exec result from `_run()` as a tuple `(action, exec_result)`. This design was chosen over storing state on the node instance for important reasons:

**Why a tuple instead of node attributes?**
- **Thread/async safety**: Storing `_last_exec_result` on the node could cause race conditions when nodes run in parallel (e.g., in `AsyncParallelBatchFlow`)
- **Immutability**: The tuple is returned directly and cannot be modified after `_run()` completes
- **Clarity**: The orchestrator explicitly receives both values, making the data flow obvious

**Backward compatibility**: Custom `_run()` implementations that return just an action (not a tuple) still work - the orchestrator handles both cases gracefully.

### Manual Error Handling (Optional)

If you need custom error handling logic (e.g., different actions based on error type), you can still use `is_error()` in `post()`:

```python
def post(self, shared, prep_res, exec_res):
    if self.is_error(exec_res):
        if "rate limit" in exec_res.message:
            return "retry_later"  # Custom action
        return "fatal_error"
    return "success"
```

### Custom Fallback (Override Default)

To customize the fallback behavior, override `exec_fallback()`:

```python
def exec_fallback(self, prep_res, exc):
    # Option 1: Re-raise to crash the flow (old behavior)
    raise exc

    # Option 2: Return a custom fallback value
    return "fallback_result"

    # Option 3: Return default NodeError (current default)
    return super().exec_fallback(prep_res, exc)
```

### Example: Summarize file

```python 
class SummarizeFile(Node):
    def prep(self, shared):
        return shared["data"]

    def exec(self, prep_res):
        if not prep_res:
            return "Empty file content"
        prompt = f"Summarize this text in 10 words: {prep_res}"
        summary = call_llm(prompt)  # might fail
        return summary

    def exec_fallback(self, prep_res, exc):
        # Provide a simple fallback instead of crashing
        return "There was an error processing your request."

    def post(self, shared, prep_res, exec_res):
        shared["summary"] = exec_res
        # Return "default" by not returning

summarize_node = SummarizeFile(max_retries=3)

# node.run() calls prep->exec->post
# If exec() fails, it retries up to 3 times before calling exec_fallback()
action_result = summarize_node.run(shared)

print("Action returned:", action_result)  # "default"
print("Summary stored:", shared["summary"])
```