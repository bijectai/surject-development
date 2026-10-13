import pytest

from biject_surject import __version__
from biject_surject.cli import main


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"surject {__version__}"


def test_no_args_prints_help(capsys):
    assert main([]) == 0
    assert "usage: surject" in capsys.readouterr().out
