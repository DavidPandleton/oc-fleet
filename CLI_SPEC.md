# CLI spec

```
oc-fleet status                 # snapshot: server up? active? last 5 done
oc-fleet dispatch "TASK" --workdir DIR [--model cutad/qwen3-8-flash-next] [--title T] [--detach]
                                 # --detach: spawn background waiter (oc-wait.py style)
oc-fleet watch [--timeout 3600] # SSE live: print completions as they land
oc-fleet sessions [N]           # table: id, title, time
oc-fleet show ID                # full: outcome + last reply (sanitized)
oc-fleet stats                  # totals
```

Exit codes: 0 ok, 1 task failed, 2 api/connection error.
Output: plain text (Hermes-friendly), NO color codes.
