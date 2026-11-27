"""Settings, with the same names and defaults as CGP's conf/config.php.

They come from three places, each overriding the one before:

1. The defaults below.
2. A TOML file, `/etc/cgp/config.toml` or the path in `CGP_CONFIG`.
3. Environment variables: `CGP_` and the upper-case setting name, e.g.
   `CGP_DATADIR=/srv/rrd` or `CGP_OVERVIEW=load,cpu,memory`. Lists are
   comma-separated; booleans are true/false.
"""

from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = PACKAGE_DIR / "data"

DEFAULTS: dict[str, Any] = {
    # collectd's datadir
    "datadir": "/var/lib/collectd/rrd",
    # types.db files; CGP's own copy is used when the first is missing
    "typesdb": ["/usr/share/collectd/types.db"],
    # rrdtool executable and extra options for every graph
    "rrdtool": "/usr/bin/rrdtool",
    "rrdtool_opts": [],
    # host categories on the index page: a list of hosts, or a regular
    # expression (Python syntax) matched against host names
    "cat": {},
    # plugins shown on a host's overview page, in this order
    "overview": ["load", "cpu", "memory", "swap"],
    # limit a plugin's graphs on the overview page, e.g.
    # {"interface": {"pi": "eth0", "t": "if_octets"}}
    "overview_filter": {},
    # default time range of a plugin's graphs, in seconds
    "time_range": {"default": 86400, "uptime": 31536000},
    # the time ranges to choose from
    "term": {
        "2hour": 3600 * 2,
        "8hour": 3600 * 8,
        "day": 86400,
        "week": 86400 * 7,
        "month": 86400 * 31,
        "quarter": 86400 * 31 * 3,
        "year": 86400 * 365,
    },
    # load averages, used memory and last update on the index page
    "showload": True,
    "showmem": True,
    "showtime": True,
    # network graphs in "bits" or "bytes"
    "network_datasize": "bytes",
    # "interactive" (charts drawn in the browser), "png" or "svg" (rrdtool
    # images); /graph.png and /graph.svg work whichever is chosen
    "graph_type": "interactive",
    # use the negative Y axis for the second line of I/O graphs
    "negative_io": False,
    # add an Nth percentile line to network graphs; false to disable
    "percentile": False,
    # smooth graphs (rrdtool -E)
    "graph_smooth": False,
    # draw min/max lines in a lighter colour in graphs of type default
    "graph_minmax": False,
    # how long browsers may cache graphs, in seconds
    "cache": 90,
    # graph sizes, and the largest that may be asked for
    "width": 500,
    "height": 200,
    "detail_width": 1000,
    "detail_height": 600,
    "max_width": None,
    "max_height": None,
    # collectd's unixsock plugin or rrdcached socket, to flush data before
    # drawing: e.g. "unix:///var/run/collectd-unixsock"; empty to disable
    "socket": None,
    # "collectd" or "rrdcached"
    "flush_type": "collectd",
    # timezone for rrdtool images when the container has none
    "default_timezone": "UTC",
    # extra or overriding plugin JSON files, merged over CGP's own
    "plugin_dir": "/etc/cgp/plugin",
}

DEFAULT_CONFIG_FILE = "/etc/cgp/config.toml"


def _from_env(name: str, default: Any, value: str) -> Any:
    if name == "percentile":
        return False if value.strip().lower() in ("", "0", "false", "no", "off") else int(value)
    if isinstance(default, bool):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(value)
    if isinstance(default, list):
        return [v.strip() for v in value.split(",") if v.strip()]
    return value or None


def _merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict) and key not in ("term", "cat"):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load(environ: os._Environ | dict | None = None) -> dict[str, Any]:
    environ = os.environ if environ is None else environ
    config = copy.deepcopy(DEFAULTS)

    path = Path(environ.get("CGP_CONFIG", DEFAULT_CONFIG_FILE))
    if path.is_file():
        with path.open("rb") as f:
            _merge(config, tomllib.load(f))

    for name, default in DEFAULTS.items():
        value = environ.get(f"CGP_{name.upper()}")
        if value is not None and not isinstance(default, dict):
            config[name] = _from_env(name, default, value)

    if isinstance(config["typesdb"], str):
        config["typesdb"] = [config["typesdb"]]
    if isinstance(config["rrdtool_opts"], str):
        config["rrdtool_opts"] = config["rrdtool_opts"].split()
    if config["max_width"] is None:
        config["max_width"] = config["detail_width"] * 2
    if config["max_height"] is None:
        config["max_height"] = config["detail_height"] * 2
    if config["graph_type"] not in ("interactive", "png", "svg"):
        config["graph_type"] = "interactive"
    config["datadir"] = str(config["datadir"]).rstrip("/") or "/"
    return config
