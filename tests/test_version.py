import re
import sys
from subprocess import run, PIPE

REGEX = r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)(?:(?:a|b|rc)\d+)?(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+(?P<buildmetadata>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"


def test_print_version():
    result = run([sys.executable, "-m", "paker", "-V"], stdout=PIPE, stderr=PIPE)
    version = result.stdout.decode().strip().replace("v", "")
    assert re.match(REGEX, version) is not None


def test_version_importable():
    from paker import __version__
    assert re.match(REGEX, __version__) is not None
