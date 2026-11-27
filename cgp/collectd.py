"""collectd's RRD tree: hosts, plugins and their files, plugin JSON and types.db.

A port of CGP's inc/collectd.inc.php, keeping its ordering rules so graphs
come out in the same order and with the same colours.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR

log = logging.getLogger(__name__)

# <plugin>[-<category, varnish only>][-<plugin instance>]/<type>[-<type instance>].rrd
FILE_RE = re.compile(
    r"""
    (?P<p>[\w_]+)                        # plugin
    (?:(?<=varnish)(?:\-(?P<c>[\w]+)))?  # category
    (?:\-(?P<pi>.+))?                    # plugin instance
    /
    (?P<t>[\w_]+)                        # type
    (?:\-(?P<ti>.+))?                    # type instance
    \.rrd
    """,
    re.VERBOSE | re.ASCII,
)

_NUMERIC_RE = re.compile(r"^[ \t\n\r\v\f]*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?[ \t\n\r\v\f]*$")

# Seconds a scan of a host's files is reused; pages ask for it many times
SCAN_TTL = 5


# --- PHP 8 ordering, for parity with CGP's sort() and array_multisort() ---

def php_cmp(a: Any, b: Any) -> int:
    """Compare like PHP 8's <=> on strings: numerically when both are numeric."""
    a = "" if a is None else str(a)
    b = "" if b is None else str(b)
    if _NUMERIC_RE.match(a) and _NUMERIC_RE.match(b):
        fa, fb = float(a), float(b)
        return (fa > fb) - (fa < fb)
    return (a > b) - (a < b)


php_key = functools.cmp_to_key(php_cmp)


def php_sorted(values) -> list:
    return sorted(values, key=php_key)


# --- the RRD tree ---

class Collectd:
    def __init__(self, config: dict):
        self.config = config
        self.datadir = config["datadir"]
        self._scans: dict[str, tuple[float, list[str]]] = {}
        self._lock = threading.Lock()

    def hosts(self) -> list[str]:
        """Every host directory in the datadir, sorted by name."""
        try:
            names = sorted(os.listdir(self.datadir))
        except OSError:
            return []
        return [n for n in names if os.path.isdir(os.path.join(self.datadir, n))]

    def host_dir(self, host: str) -> str | None:
        """The host's directory, or None if it isn't one inside the datadir."""
        if not host or host in (".", "..") or "/" in host:
            return None
        path = os.path.join(self.datadir, host)
        return path if os.path.isdir(path) else None

    def _rrd_files(self, host: str) -> list[str]:
        """The host's RRD files, relative to its directory, at least one level deep."""
        hostdir = self.host_dir(host)
        if not hostdir:
            return []
        now = time.monotonic()
        with self._lock:
            cached = self._scans.get(host)
            if cached and now - cached[0] < SCAN_TTL:
                return cached[1]
        files = []
        for root, dirs, names in os.walk(hostdir):
            dirs.sort()
            rel = os.path.relpath(root, hostdir)
            if rel == ".":
                continue
            for name in sorted(names):
                if name.endswith(".rrd"):
                    files.append(f"{rel}/{name}")
        with self._lock:
            self._scans[host] = (now, files)
        return files

    def plugindata(self, host: str, plugin: str | None = None) -> list[dict[str, str]]:
        """One {p, c, pi, t, ti} dict per RRD file, optionally for one plugin."""
        data = []
        for item in self._rrd_files(host):
            m = FILE_RE.search(item)
            if not m:
                continue
            entry = {k: m.group(k) or "" for k in ("p", "c", "pi", "t", "ti")}
            if plugin is None or entry["p"] == plugin:
                data.append(entry)
        return data

    def plugins(self, host: str) -> list[str]:
        seen = []
        for item in self.plugindata(host):
            if item["p"] not in seen:
                seen.append(item["p"])
        return php_sorted(seen)

    # --- grouping files into graphs ---

    def group_plugindata(self, plugindata: list[dict], plugins: "Plugins") -> list[dict]:
        """Merge files into one entry per graph, dropping duplicates."""
        data = []
        seen = set()
        for item in plugindata:
            item = dict(item)
            if plugins.merge_instances(item["p"], item["t"]):
                # one graph per type (instance) with a line per plugin instance
                item.pop("pi", None)
            elif not (
                item["p"] == "libvirt"
                or (item["p"] == "snmp" and item["t"] == "if_octets")
                or (item["p"] == "vmem" and item["t"] == "vmpage_io")
            ):
                # type instances share a graph
                item.pop("ti", None)
            key = tuple(sorted(item.items()))
            if key not in seen:
                seen.add(key)
                data.append(item)
        return data

    @staticmethod
    def plugin_sort(data: list[dict]) -> list[dict]:
        def cmp(a, b):
            for k in ("c", "pi", "t", "ti"):
                r = php_cmp(a.get(k), b.get(k))
                if r:
                    return r
            return 0

        return sorted(data, key=functools.cmp_to_key(cmp))

    def graphs(self, host: str, plugin: str, plugins: "Plugins") -> list[dict]:
        """The graphs of one of a host's plugins, as {p, c, pi, t, ti} dicts."""
        data = self.plugindata(host, plugin)
        return self.plugin_sort(self.group_plugindata(data, plugins)) if data else []


# --- plugin JSON ---

def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class Plugins:
    """Plugin JSON files: CGP's own, its local overrides, then the config's."""

    def __init__(self, config: dict):
        self.dirs = [
            DATA_DIR / "plugin",
            DATA_DIR / "plugin" / "local",
            Path(config["plugin_dir"]) if config.get("plugin_dir") else None,
        ]
        self._cache: dict[str, tuple[tuple, dict]] = {}

    def load(self, plugin: str | None) -> dict:
        if not plugin or not re.fullmatch(r"\w+", plugin):
            return {}
        files = [d / f"{plugin}.json" for d in self.dirs if d]
        stamp = tuple(f.stat().st_mtime if f.is_file() else None for f in files)
        cached = self._cache.get(plugin)
        if cached and cached[0] == stamp:
            return cached[1]
        merged: dict = {}
        for f in files:
            if not f.is_file():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log.error("CGP: invalid json in %s: %s", f, e)
                continue
            if isinstance(data, dict):
                _deep_merge(merged, data)
            else:
                log.error("CGP: invalid json in %s", f)
        self._cache[plugin] = (stamp, merged)
        return merged

    def type_json(self, plugin: str | None, type_: str | None) -> dict:
        value = self.load(plugin).get(type_ or "")
        return value if isinstance(value, dict) else {}

    def merge_instances(self, plugin: str | None, type_: str | None) -> bool:
        return bool(self.type_json(plugin, type_).get("merge_instances"))

    def graph_type(self, plugin: str | None, type_: str | None) -> str:
        return self.type_json(plugin, type_).get("type", "default")


# --- types.db ---

_TYPE_RE = re.compile(r"^(?P<dataset>[\w_]+)\s+(?P<datasources>.*)")
_DS_RE = re.compile(r"^(?P<dsname>\w+):(?P<dstype>\w+):(?P<min>[\-\dU\.]+):(?P<max>[\dU\.]+)")


class TypesDB:
    def __init__(self, config: dict):
        files = list(config["typesdb"]) or [""]
        if not os.path.isfile(files[0]):
            files[0] = str(DATA_DIR / "types.db")
        self.files = files
        self._stamp: tuple | None = None
        self._types: dict[str, dict[str, dict]] = {}

    def types(self) -> dict[str, dict[str, dict]]:
        stamp = tuple(os.path.getmtime(f) if os.path.isfile(f) else None for f in self.files)
        if stamp != self._stamp:
            self._types = self._parse()
            self._stamp = stamp
        return self._types

    def _parse(self) -> dict[str, dict[str, dict]]:
        types: dict[str, dict[str, dict]] = {}
        for path in self.files:
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = _TYPE_RE.match(line)
                    if not m:
                        continue
                    dataset = m["dataset"]
                    for ds in m["datasources"].rstrip("\n").split(", "):
                        d = _DS_RE.match(ds)
                        if not d:
                            log.error('CGP: DS "%s" from dataset "%s" did not match', ds, dataset)
                            continue
                        types.setdefault(dataset, {})[d["dsname"]] = {
                            "dstype": d["dstype"], "min": d["min"], "max": d["max"],
                        }
        return types

    def data_sources(self, type_: str | None) -> list[str] | None:
        ds = self.types().get(type_ or "")
        return list(ds) if ds else None
