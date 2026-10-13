# Surject in your own Python code

If you write your own tools or your own agent loop, guard the tools
directly. No launcher is needed.

```python
import biject_surject as surject

@surject.guard
def write_file(path: str, content: str) -> None:
    with open(path, "w") as f:
        f.write(content)

@surject.guard(name="sql")          # override the tool name used in rules
async def run_query(query: str) -> list:
    ...
```

`@surject.guard` works on regular functions, async functions and methods.
It checks each call against the nearest `surject.yaml` before the function
runs. Positional arguments are matched to their parameter names, so
`write_file("out.csv", "...")` is checked as `{path: "out.csv", content: "..."}`.

In enforce mode, a blocked call raises `surject.PolicyViolation` (a
`PermissionError`) instead of running. Its message explains why, so you
can return it to your model as the tool result:

```python
try:
    result = write_file(**args)
except surject.PolicyViolation as e:
    result = str(e)   # "surject blocked tool 'write_file': ..."
```

## Checking without a decorator

```python
verdict = surject.check("send_email", {"to": "x@example.com", "body": "..."})
if verdict.decision == "block":
    ...
```

`surject.check` never raises. It returns a verdict with `decision`
(`allow`, `flag` or `block`) and the `violations` it found.

## Using a specific policy

```python
monitor = surject.Monitor.from_file("config/surject.yaml")

@monitor.guard
def deploy(env: str) -> None:
    ...
```

## Attaching to frameworks from code

To get the `surject watch` behaviour without the launcher, for example in a
notebook or a web server:

```python
from biject_surject import autopatch
autopatch.install()
```
