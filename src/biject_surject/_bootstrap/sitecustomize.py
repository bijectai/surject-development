# Loaded automatically by Python in processes started by `surject watch`,
# which puts this directory first on PYTHONPATH. It must never break the
# agent: every failure is reported once and then ignored.
import os
import sys


def _chain_existing_sitecustomize():
    """Run the sitecustomize we're shadowing, if the environment has one."""
    here = os.path.dirname(os.path.abspath(__file__))
    original_path = list(sys.path)
    try:
        sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != here]
        mod = sys.modules.pop("sitecustomize", None)
        try:
            import importlib
            importlib.import_module("sitecustomize")
        except ImportError:
            pass
        finally:
            if mod is not None:
                sys.modules["sitecustomize"] = mod
    except Exception:
        pass
    finally:
        sys.path[:] = original_path


def _attach():
    try:
        from biject_surject import autopatch
    except ImportError:
        sys.stderr.write(
            "[surject] biject-surject is not installed in the Python running "
            "your agent (" + sys.executable + "); install it there with "
            "`pip install biject-surject`. Your agent is running unchecked.\n"
        )
        return
    try:
        autopatch.install()
    except Exception as exc:
        sys.stderr.write("[surject] could not attach (%s); your agent is "
                         "running unchecked.\n" % type(exc).__name__)


if os.environ.get("SURJECT_WATCH") == "1":
    _chain_existing_sitecustomize()
    _attach()
