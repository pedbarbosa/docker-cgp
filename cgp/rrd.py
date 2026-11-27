"""Running rrdtool, and asking collectd or rrdcached to flush data first.

rrdtool runs without a shell, so arguments need no shell escaping; values
inside graph definitions are still escaped for rrdtool itself (rrd_escape).
"""

from __future__ import annotations

import logging
import math
import os
import socket
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

TIMEOUT = 60


class RRDError(Exception):
    pass


def _env(config: dict) -> dict:
    env = dict(os.environ)
    # rrdtool draws times in the container's timezone; fall back to the
    # configured one when there is none
    if "TZ" not in env and not os.path.exists("/etc/localtime"):
        env["TZ"] = config.get("default_timezone") or "UTC"
    return env


def run(config: dict, args: list[str]) -> bytes:
    cmd = [config["rrdtool"], *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT, env=_env(config))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RRDError(str(e)) from e
    if proc.returncode != 0:
        raise RRDError(f"rrdtool exited {proc.returncode}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def info(config: dict, path: str) -> dict[str, str] | None:
    """`rrdtool info` as a dict of strings, or None if the file is missing."""
    if not os.path.isfile(path):
        return None
    try:
        out = run(config, ["info", path]).decode(errors="replace")
    except RRDError as e:
        log.warning("CGP: %s", e)
        return None
    result = {}
    for line in out.splitlines():
        key, sep, value = line.partition(" = ")
        if sep:
            result[key] = value.replace('"', "")
    return result


def info_float(info_: dict | None, key: str) -> float | None:
    if not info_ or key not in info_:
        return None
    try:
        value = float(info_[key])
    except ValueError:
        return None
    return None if math.isnan(value) else value


@dataclass
class Xport:
    start: int
    end: int
    step: int
    timestamps: list[int] = field(default_factory=list)
    columns: dict[str, list[float | None]] = field(default_factory=dict)


def xport(config: dict, start: str, end: str, maxrows: int, defs: list[str], vnames: list[str]) -> Xport:
    """Export vnames computed from defs, as columns of floats (None when unknown)."""
    # XPORT legends are the vnames, so columns can be found by name
    args = ["xport", "-s", start, "-e", end, "-m", str(maxrows), *defs]
    args += [f"XPORT:{v}:{v}" for v in vnames]
    root = ET.fromstring(run(config, args))
    meta = root.find("meta")
    start_ = int(meta.findtext("start"))
    step = int(meta.findtext("step"))
    legend = [e.text or "" for e in meta.find("legend").findall("entry")]
    result = Xport(start=start_, end=int(meta.findtext("end")), step=step,
                   columns={name: [] for name in legend})
    for i, row in enumerate(root.find("data").findall("row")):
        t = row.findtext("t")
        result.timestamps.append(int(t) if t is not None else start_ + i * step)
        for name, v in zip(legend, row.findall("v")):
            try:
                value = float(v.text)
            except (TypeError, ValueError):
                value = math.nan
            result.columns[name].append(None if math.isnan(value) or math.isinf(value) else value)
    return result


# --- flushing ---

def _socket_cmd(sock_file, cmd: str) -> bool:
    try:
        sock_file.write(cmd.encode())
        sock_file.flush()
        resp = sock_file.readline()
    except OSError as e:
        log.error("ERROR: Failed to talk to the flush socket for command %s: %s", cmd.strip(), e)
        return False
    if not resp:
        log.error("ERROR: Failed to read response from collectd for command: %s", cmd.strip())
        return False
    try:
        n = int(resp.split()[0])
    except (ValueError, IndexError):
        n = 0
    for _ in range(max(n, 0)):
        sock_file.readline()
    return True


def flush(config: dict, identifiers: list[str]) -> bool:
    """Tell collectd (unixsock) or rrdcached to flush the identifiers' data."""
    target = config.get("socket")
    if not target or not identifiers:
        return False
    path = target[len("unix://"):] if target.startswith("unix://") else target
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(path)
            with s.makefile("rwb") as f:
                if config.get("flush_type") == "rrdcached":
                    for ident in identifiers:
                        _socket_cmd(f, "FLUSH {}.rrd\n".format(ident.replace(" ", "\\ ")))
                else:
                    cmd = "FLUSH" + "".join(f' identifier="{ident}"' for ident in identifiers) + "\n"
                    _socket_cmd(f, cmd)
    except OSError as e:
        log.error("ERROR: Failed to open unix-socket to %s: %s", target, e)
        return False
    return True
