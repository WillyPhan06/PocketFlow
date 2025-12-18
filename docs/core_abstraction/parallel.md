---
layout: default
title: "(Advanced) Parallel"
parent: "Core Abstraction"
nav_order: 6
---

# (Advanced) Parallel

**Parallel** Nodes and Flows let you run multiple **Async** Nodes and Flows  **concurrently**—for example, summarizing multiple texts at once. This can improve performance by overlapping I/O and compute. 

> Because of Python’s GIL, parallel nodes and flows can’t truly parallelize CPU-bound tasks (e.g., heavy numerical computations). However, they excel at overlapping I/O-bound work—like LLM calls, database queries, API requests, or file I/O.
{: .warning }

> - **Ensure Tasks Are Independent**: If each item depends on the output of a previous item, **do not** parallelize.
>
> - **Beware of Rate Limits**: Parallel calls can **quickly** trigger rate limits on LLM services. Use the built-in `concurrency_limit` parameter to control max parallel executions.
>
> - **Consider Single-Node Batch APIs**: Some LLMs offer a **batch inference** API where you can send multiple prompts in a single call. This is more complex to implement but can be more efficient than launching many parallel requests and mitigates rate limits.
{: .best-practice }

## AsyncParallelBatchNode

Like **AsyncBatchNode**, but run `exec_async()` in **parallel**:

```python
class ParallelSummaries(AsyncParallelBatchNode):
    async def prep_async(self, shared):
        # e.g., multiple texts
        return shared["texts"]

    async def exec_async(self, text):
        prompt = f"Summarize: {text}"
        return await call_llm_async(prompt)

    async def post_async(self, shared, prep_res, exec_res_list):
        shared["summary"] = "\n\n".join(exec_res_list)
        return "default"

node = ParallelSummaries()
flow = AsyncFlow(start=node)
```

### Concurrency Limit

To prevent resource overload or rate limiting, use `concurrency_limit` to control the maximum number of parallel executions:

```python
# Limit to 5 concurrent API calls
node = ParallelSummaries(concurrency_limit=5)
```

When `concurrency_limit` is set, only that many `exec_async()` calls will run simultaneously. The rest will wait until a slot becomes available. If not set, all items are processed in parallel without limit.

## AsyncParallelBatchFlow

Parallel version of **BatchFlow**. Each iteration of the sub-flow runs **concurrently** using different parameters:

```python
class SummarizeMultipleFiles(AsyncParallelBatchFlow):
    async def prep_async(self, shared):
        return [{"filename": f} for f in shared["files"]]

sub_flow = AsyncFlow(start=LoadAndSummarizeFile())
parallel_flow = SummarizeMultipleFiles(start=sub_flow)
await parallel_flow.run_async(shared)
```

### Concurrency Limit

Like `AsyncParallelBatchNode`, you can limit the number of concurrent flow executions:

```python
# Process at most 3 files concurrently
parallel_flow = SummarizeMultipleFiles(start=sub_flow, concurrency_limit=3)
```

This is especially useful when each sub-flow makes API calls that might hit rate limits, or when processing many items that could exhaust system resources.