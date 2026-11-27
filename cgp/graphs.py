"""Graph definitions: a port of CGP's graph.php and type/*.class.php.

`build_graph()` turns request parameters into a Graph, which can produce:

- `rrd_gen_graph()`: the rrdtool graph arguments, the same as CGP's PHP
  produces, for PNG/SVG images;
- `chart()`: the data for an interactive chart, exported with `rrdtool xport`
  from the same DEF/CDEF definitions, with the legend's statistics.
"""

from __future__ import annotations

import glob
import math
import os
import re
import zlib
from typing import Any

from . import rrd
from .collectd import Collectd, Plugins, TypesDB, php_cmp, php_key, php_sorted


def crc32hex(value: Any) -> str:
    return format(zlib.crc32(("" if value is None else str(value)).encode()), "x")


def php_empty(value: Any) -> bool:
    return value is None or value == "" or value == "0" or value == 0 or value is False


def plain_decimal(value: Any) -> str:
    """A number as rrdtool reads it, never in exponent notation (1.2E-5)."""
    return f"{float(value):.15f}".rstrip("0").rstrip(".")


def rrd_escape(value: Any) -> str:
    # https://oss.oetiker.ch/rrdtool/doc/rrdgraph_graph.en.html#IEscaping_the_colon
    return str(value).replace("\\", "\\\\").replace(":", "\\:")


def validate_color(color: Any) -> str:
    return color if isinstance(color, str) and re.fullmatch(r"[0-9a-f]{6}", color) else "000000"


def faded_color(fgc: Any, bgc: str = "ffffff", percent: float = 0.25) -> str:
    fgc = validate_color(fgc)
    out = ""
    for i in (0, 2, 4):
        fg = int(fgc[i:i + 2], 16)
        try:
            bg = int(bgc[i:i + 2], 16)
        except ValueError:
            bg = 0
        out += format(int(round_half_up(percent * fg + (1.0 - percent) * bg)), "02x")
    return out


def round_half_up(x: float) -> float:
    # PHP's round() rounds halves away from zero
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def _pad_bytes(value: str, width: int) -> str:
    return value + " " * max(width - len(value.encode()), 0)


class Graph:
    """Type_Base: the files, sources, legend and colours of one graph."""

    kind = "default"

    def __init__(self, config: dict, args: dict):
        self.config = config
        self.datadir = config["datadir"]
        self.rrdtool_opts: list[str] = list(config.get("rrdtool_opts") or [])
        self.cache = config["cache"]
        self.args = {
            "host": args.get("h"),
            "plugin": args.get("p"),
            "pinstance": args.get("pi"),
            "category": args.get("c"),
            "type": args.get("t"),
            "tinstance": args.get("ti"),
        }
        self.seconds = args.get("s")
        self.data_sources: list[str] = ["value"]
        self.order: list[str] | None = None
        self.legend: dict[str, str] = {}
        self.colors: dict[str, str] = {}
        self.rrd_title = default_title(args)
        self.rrd_vertical: str | None = None
        self.rrd_format = "%5.1lf%s"
        self.scale: Any = 1
        self.base: Any = None
        self.width = args.get("x") or config["width"]
        self.height = args.get("y") or config["height"]
        self.negative_io = config["negative_io"]
        self.percentile: Any = False
        self.graph_smooth = config["graph_smooth"]
        self.graph_minmax = config["graph_minmax"]
        self.merge_instances = False
        self.tinstances: list[str] = []
        self.files: dict[str, str] = {}
        self.identifiers: dict[str, str] = {}
        self.rrd_files()

    # --- files ---

    def get_filenames(self) -> list[str]:
        a = self.args
        plugin_dir = f"{a['host']}/{a['plugin'] or ''}" + (f"-{a['category']}" if a["category"] else "")
        type_file = (a["type"] or "") + (f"-{a['tinstance']}" if a["tinstance"] else "")
        if self.merge_instances:
            # the same type in every plugin instance's directory
            identifier = glob.escape(plugin_dir) + "-*/" + glob.escape(type_file)
        else:
            identifier = glob.escape(
                plugin_dir + (f"-{a['pinstance']}" if a["pinstance"] else "") + "/" + type_file)
        wildcard = "." if a["tinstance"] else "[-.]*"
        return sorted(glob.glob(f"{glob.escape(self.datadir)}/{identifier}{wildcard}rrd"))

    def rrd_files(self) -> None:
        self.tinstances, self.files, self.identifiers = [], {}, {}
        prefix = re.compile("^" + re.escape(self.datadir) + r"/(.*)\.rrd$")
        for filename in self.get_filenames():
            basename = os.path.basename(filename)[:-len(".rrd")]
            pos = basename.find("-")
            instance = basename[pos + 1:] if pos > 0 else "value"
            # name each line after its plugin instance, e.g. the disk, and
            # its type instance too when the graph isn't for a single one
            if self.merge_instances:
                dirname = os.path.basename(os.path.dirname(filename))
                pos = dirname.find("-")
                pinstance = dirname[pos + 1:] if pos >= 0 else dirname[1:]
                instance = pinstance if (instance == "value" or self.args["tinstance"]) else f"{pinstance}-{instance}"
            self.tinstances.append(instance)
            self.files[instance] = filename
            self.identifiers[instance] = prefix.sub(r"\1", filename)
        self.tinstances = php_sorted(self.tinstances)
        self.files = dict(sorted(self.files.items(), key=lambda kv: php_key(kv[0])))
        self.identifiers = dict(sorted(self.identifiers.items(), key=lambda kv: php_key(kv[0])))

    def parse_filename(self, filename: str) -> str:
        return rrd_escape(filename)

    # --- sources, legend and colours ---

    def rrd_get_sources(self) -> list[str]:
        if len(self.files) > 1:
            if isinstance(self.order, list):
                ordered = [o for o in self.order if o in self.tinstances]
                merged: list[str] = []
                for t in ordered + self.tinstances:
                    if t not in merged:
                        merged.append(t)
                self.tinstances = merged
            if len(self.data_sources) > 1:
                sources = [f"{f}-{s}" for f in self.tinstances for s in self.data_sources]
            else:
                sources = list(self.tinstances)
        elif self.data_sources == ["value"]:
            sources = list(self.tinstances)
        else:
            sources = list(self.data_sources)
        self.parse_legend(sources)
        return sources

    def parse_legend(self, sources: list[str]) -> None:
        legend = dict(self.legend)
        for s in sources:
            legend.setdefault(s, s)
        width = max((len(str(v).encode()) for v in legend.values()), default=0)
        self.legend = {k: _pad_bytes(str(v), width) for k, v in legend.items()}

    def rainbow_colors(self) -> None:
        sources = self.rrd_get_sources()
        n = len(sources)
        for c, ds in enumerate(sources):
            h = 360 - (c * (330 / (n - 1))) if n > 1 else 360
            h = math.fmod(h, 360) / 60
            f = h - math.floor(h)
            q = [0, 0, f, 1, 1, 1 - f]
            self.colors[ds] = "".join(
                format(int(round_half_up(q[(int(math.floor(h)) + j) % 6] * 255)), "02x") for j in (4, 2, 0))

    def prepare(self) -> None:
        """What CGP's rrd_graph() does before drawing: flush, then colours."""
        rrd.flush(self.config, list(self.identifiers.values()))
        own = dict(self.colors)
        self.rainbow_colors()
        self.colors = {**self.colors, **own}

    def color(self, source: str) -> str:
        return self.colors.get(source, "000000")

    def legend_for(self, source: str) -> str:
        value = self.legend.get(source)
        return source if php_empty(value) else value

    def label_for(self, source: str) -> str:
        """The legend without rrdtool's alignment padding."""
        return self.legend_for(source).rstrip() or source

    # --- rrdtool options ---

    def scaled(self) -> bool:
        return not php_empty(self.scale)

    def rrd_options(self) -> list[str]:
        opts = list(self.rrdtool_opts)
        if self.graph_smooth:
            opts.append("-E")
        if not php_empty(self.base):
            opts += ["--base", str(self.base)]
        if "-l" not in opts:
            opts += ["-l", "0"]
        opts += [
            "-w", str(self.width) if _is_numeric(self.width) else "400",
            "-h", str(self.height) if _is_numeric(self.height) else "175",
            "-t", self.rrd_title,
        ]
        if self.rrd_vertical:
            opts += ["-v", self.rrd_vertical]
        opts += ["-s", "e-%d" % int(float(self.seconds)) if _is_numeric(self.seconds) else "e-86400"]
        return opts

    def defs(self, sources: list[str], cfs=("MIN", "AVERAGE", "MAX"), raw_avg: bool = False) -> list[str]:
        """DEFs (and scaling CDEFs) for every source, as the type classes write them."""
        raw = "_raw" if self.scaled() else ""
        out = []
        i = 0
        for tinstance in self.tinstances:
            for ds in self.data_sources:
                h = crc32hex(sources[i] if i < len(sources) else "")
                f = self.parse_filename(self.files[tinstance])
                names = {"MIN": "min", "AVERAGE": "avg", "MAX": "max"}
                for cf in cfs:
                    suffix = "_raw" if (raw_avg and cf == "AVERAGE") else raw
                    out.append(f"DEF:{names[cf]}_{h}{suffix}={f}:{ds}:{cf}")
                i += 1
        if self.scaled():
            i = 0
            for _ in self.tinstances:
                for _ in self.data_sources:
                    h = crc32hex(sources[i] if i < len(sources) else "")
                    for name in ("min", "avg", "max"):
                        out.append(f"CDEF:{name}_{h}={name}_{h}_raw,{self.scale},*")
                    i += 1
        return out

    def gprints(self, h: str, last_eol: bool = True) -> list[str]:
        fmt = self.rrd_format
        return [
            f"GPRINT:min_{h}:MIN:{fmt} Min,",
            f"GPRINT:avg_{h}:AVERAGE:{fmt} Avg,",
            f"GPRINT:max_{h}:MAX:{fmt} Max,",
            f"GPRINT:avg_{h}:LAST:{fmt} Last" + ("\\l" if last_eol else ""),
        ]

    def rrd_gen_graph(self) -> list[str]:
        raise NotImplementedError

    # --- interactive charts ---

    def chart_plan(self, sources: list[str]) -> tuple[list[dict], list[str]]:
        """The series to draw, and the vnames to export for them."""
        raise NotImplementedError

    def chart(self, start: str, end: str, maxrows: int) -> dict:
        self.prepare()
        args = self.rrd_gen_graph()
        sources = self.sources
        definitions = [a for a in args if a.startswith(("DEF:", "CDEF:"))]
        series, vnames = self.chart_plan(sources)
        data = rrd.xport(self.config, start, end, maxrows, definitions, list(dict.fromkeys(vnames)))
        columns = data.columns
        out_series = []
        for s in series:
            if "const" in s:
                value = s.pop("const")(columns, data.step)
                s["values"] = [value] * len(data.timestamps) if value is not None else [None] * len(data.timestamps)
            else:
                s["values"] = columns.get(s.pop("vname"), [])
            hover = s.pop("hover", None)
            if hover:
                s["hover"] = columns.get(hover, [])
            stats = s.pop("stats", None)
            if stats:
                s["stats"] = {k: fn(columns, data.step) for k, fn in stats.items()}
            out_series.append(s)
        return {
            "kind": self.kind,
            "title": self.rrd_title,
            "vertical": self.rrd_vertical or "",
            "format": parse_format(self.rrd_format, self.base),
            "ylim": y_limits(self.rrd_options()),
            "start": data.start,
            "end": data.end,
            "step": data.step,
            "timestamps": data.timestamps,
            "series": out_series,
        }


def _is_numeric(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


# --- statistics over exported columns, as rrdtool's VDEFs compute them ---

def _vals(col):
    return [v for v in col if v is not None]


def stat(kind: str, vname: str, param: float | None = None):
    def compute(columns: dict, step: int):
        col = columns.get(vname, [])
        vals = _vals(col)
        if kind == "pct":
            # unknown values sort below everything, as in rrdtool's PERCENT
            ordered = sorted(col, key=lambda v: -math.inf if v is None else v)
            if not ordered:
                return None
            return ordered[int((len(ordered) - 1) * param / 100)]
        if not vals:
            return None
        if kind == "min":
            return min(vals)
        if kind == "max":
            return max(vals)
        if kind == "avg":
            return sum(vals) / len(vals)
        if kind == "last":
            return vals[-1]
        if kind == "total":
            return sum(vals) * step
        raise ValueError(kind)

    return compute


def parse_format(fmt: str, base: Any) -> dict:
    """How to print values from an rrdtool format, e.g. '%5.1lf%sB'."""
    m = re.search(r"%-?\d*(?:\.(\d+))?l?[fFeEgG]", fmt)
    decimals = int(m.group(1)) if m and m.group(1) is not None else 2
    rest = fmt[m.end():] if m else ""
    prefix = fmt[:m.start()] if m else ""
    si = "%s" in rest or "%S" in rest
    suffix = rest.replace("%s", "").replace("%S", "").replace("%%", "%")
    return {
        "decimals": decimals,
        "si": si,
        "base": 1024 if str(base) == "1024" else 1000,
        "prefix": prefix.replace("%%", "%"),
        "suffix": suffix,
    }


def y_limits(opts: list[str]) -> dict:
    limits: dict[str, Any] = {"min": None, "max": None, "rigid": False, "log": False}
    i = 0
    while i < len(opts):
        o = opts[i]
        nxt = opts[i + 1] if i + 1 < len(opts) else None
        if o in ("-l", "--lower-limit") and _is_numeric(nxt):
            limits["min"] = float(nxt)
            i += 1
        elif o in ("-u", "--upper-limit") and _is_numeric(nxt):
            limits["max"] = float(nxt)
            i += 1
        elif o.startswith("--lower-limit=") and _is_numeric(o.split("=", 1)[1]):
            limits["min"] = float(o.split("=", 1)[1])
        elif o.startswith("--upper-limit=") and _is_numeric(o.split("=", 1)[1]):
            limits["max"] = float(o.split("=", 1)[1])
        elif o in ("-r", "--rigid"):
            limits["rigid"] = True
        elif o in ("-o", "--logarithmic"):
            limits["log"] = True
        i += 1
    return limits


def _series(label, color, vname, *, fill=None, hover=None, dash=False, legend=True, stats=None, width=1.5):
    s = {"label": label, "color": "#" + validate_color(color), "vname": vname, "dash": dash,
         "legend": legend, "width": width}
    if fill:
        s["fill"] = "#" + validate_color(fill)
    if hover:
        s["hover"] = hover
    if stats:
        s["stats"] = stats
    return s


def _const(label, color, fn, *, stats_key):
    s = {"label": label, "color": "#" + validate_color(color), "dash": True, "legend": True, "width": 1.5,
         "const": fn, "stats": {stats_key: fn}}
    return s


class DefaultGraph(Graph):
    kind = "default"

    def rrd_gen_graph(self) -> list[str]:
        g = self.rrd_options()
        sources = self.sources = self.rrd_get_sources()
        g += self.defs(sources)
        if self.graph_minmax:
            for source in sources:
                faded = faded_color(self.color(source))
                g.append(f"LINE1:max_{crc32hex(source)}#{faded}")
                g.append(f"LINE1:min_{crc32hex(source)}#{faded}")
        for source in sources:
            h = crc32hex(source)
            g.append(f"LINE1:avg_{h}#{validate_color(self.color(source))}:{rrd_escape(self.legend_for(source))}")
            g += self.gprints(h)
        return g

    def chart_plan(self, sources):
        series, vnames = [], []
        if self.graph_minmax:
            for source in sources:
                h = crc32hex(source)
                faded = faded_color(self.color(source))
                series.append(_series("max", faded, f"max_{h}", legend=False, width=1))
                series.append(_series("min", faded, f"min_{h}", legend=False, width=1))
        for source in sources:
            h = crc32hex(source)
            vnames += [f"avg_{h}", f"min_{h}", f"max_{h}"]
            series.append(_series(self.label_for(source), self.color(source), f"avg_{h}", stats={
                "min": stat("min", f"min_{h}"), "avg": stat("avg", f"avg_{h}"),
                "max": stat("max", f"max_{h}"), "last": stat("last", f"avg_{h}"),
            }))
        return series, vnames


class StackedGraph(Graph):
    kind = "stacked"

    def area_defs(self, sources, value="avg_{h}", op="ADDNAN") -> list[str]:
        out = []
        for i in range(len(sources) - 1, -1, -1):
            h = crc32hex(sources[i])
            v = value.format(h=h)
            if i == len(sources) - 1:
                out.append(f"CDEF:area_{h}={v}")
            else:
                out.append(f"CDEF:area_{h}=area_{crc32hex(sources[i + 1])},{v},{op}")
        return out

    def rrd_gen_graph(self) -> list[str]:
        g = self.rrd_options()
        sources = self.sources = self.rrd_get_sources()
        g += self.defs(sources)
        g += self.area_defs(sources)
        for source in sources:
            g.append(f"AREA:area_{crc32hex(source)}#{faded_color(self.color(source))}")
        for source in sources:
            h = crc32hex(source)
            g.append(f"LINE1:area_{h}#{validate_color(self.color(source))}:{rrd_escape(self.legend_for(source))}")
            g += self.gprints(h)
        return g

    def chart_plan(self, sources):
        series, vnames = [], []
        for source in sources:
            h = crc32hex(source)
            vnames += [f"area_{h}", f"avg_{h}", f"min_{h}", f"max_{h}"]
            series.append(_series(self.label_for(source), self.color(source), f"area_{h}",
                                  fill=faded_color(self.color(source)), hover=f"avg_{h}", stats={
                                      "min": stat("min", f"min_{h}"), "avg": stat("avg", f"avg_{h}"),
                                      "max": stat("max", f"max_{h}"), "last": stat("last", f"avg_{h}"),
                                  }))
        return series, vnames


class IOGraph(Graph):
    kind = "io"

    def rrd_gen_graph(self) -> list[str]:
        g = self.rrd_options()
        sources = self.sources = self.rrd_get_sources()
        # expects a read and a write source; with fewer, draws a single line
        two = len(sources) >= 2
        raw = "_raw" if self.scaled() else ""
        i = 0
        for tinstance in self.tinstances:
            for ds in self.data_sources:
                if i >= len(sources):
                    break
                h = crc32hex(sources[i])
                f = self.parse_filename(self.files[tinstance])
                g.append(f"DEF:min_{h}{raw}={f}:{ds}:MIN")
                g.append(f"DEF:avg_{h}_raw={f}:{ds}:AVERAGE")
                g.append(f"DEF:max_{h}{raw}={f}:{ds}:MAX")
                if not self.scaled():
                    g.append(f"VDEF:tot_{h}=avg_{h},TOTAL")
                i += 1
            else:
                continue
            break
        if self.scaled():
            i = 0
            for _ in self.tinstances:
                for _ in self.data_sources:
                    if i >= len(sources):
                        break
                    h = crc32hex(sources[i])
                    for name in ("min", "avg", "max"):
                        g.append(f"CDEF:{name}_{h}={name}_{h}_raw,{self.scale},*")
                    if two and i % 2 == 1:
                        g.append(f"CDEF:avg_{h}_neg=avg_{h}_raw,{'-' if self.negative_io else ''}{self.scale},*")
                    g.append(f"VDEF:tot_{h}=avg_{h},TOTAL")
                    if self.percentile:
                        g.append(f"VDEF:pct_{h}=avg_{h},{self.percentile},PERCENT")
                    if self.percentile and self.negative_io and two and i % 2 == 1:
                        g.append(f"VDEF:pct_{h}_neg=avg_{h}_neg,{100 - int(self.percentile)},PERCENT")
                    i += 1
                else:
                    continue
                break
        if two:
            h0, h1 = crc32hex(sources[0]), crc32hex(sources[1])
            g.append(f"CDEF:overlap=avg_{h0},avg_{h1}_neg,LT,avg_{h0},avg_{h1}_neg,IF")
        for i, source in enumerate(sources):
            neg = "_neg" if two and i % 2 == 1 else ""
            g.append(f"AREA:avg_{crc32hex(source)}{neg}#{faded_color(self.color(source))}")
        if two and not self.negative_io:
            g.append("AREA:overlap#" + faded_color(faded_color(self.color(sources[0])),
                                                   faded_color(self.color(sources[1]))))
        for i, source in enumerate(sources):
            h = crc32hex(source)
            neg = "_neg" if two and i % 2 == 1 else ""
            g.append(f"LINE1:avg_{h}{neg}#{self.color(source)}:{rrd_escape(self.legend_for(source))}")
            g += self.gprints(h, last_eol=False)
            g.append(f"GPRINT:tot_{h}:{self.rrd_format} Total\\l")
        if self.percentile:
            g.append("COMMENT: \\l")
            for i, source in enumerate(sources):
                h = crc32hex(source)
                neg = "_neg" if two and i % 2 == 1 and self.negative_io else ""
                g.append(f"LINE:pct_{h}{neg}#{faded_color(self.color(source), '000000', 0.6)}:"
                         f"{self.percentile}th Percentile {rrd_escape(self.legend_for(source))}")
                g.append(f"GPRINT:pct_{h}:{self.rrd_format}\\l")
        return g

    def chart_plan(self, sources):
        series, vnames = [], []
        two = len(sources) >= 2
        for i, source in enumerate(sources):
            h = crc32hex(source)
            plotted = f"avg_{h}_neg" if two and i % 2 == 1 else f"avg_{h}"
            vnames += [plotted, f"avg_{h}", f"min_{h}", f"max_{h}"]
            series.append(_series(self.label_for(source), self.color(source), plotted,
                                  fill=faded_color(self.color(source)), hover=f"avg_{h}", stats={
                                      "min": stat("min", f"min_{h}"), "avg": stat("avg", f"avg_{h}"),
                                      "max": stat("max", f"max_{h}"), "last": stat("last", f"avg_{h}"),
                                      "total": stat("total", f"avg_{h}"),
                                  }))
        if self.percentile:
            p = float(self.percentile)
            for i, source in enumerate(sources):
                h = crc32hex(source)
                neg = two and i % 2 == 1 and self.negative_io
                fn = stat("pct", f"avg_{h}_neg", 100 - p) if neg else stat("pct", f"avg_{h}", p)
                if neg:
                    vnames.append(f"avg_{h}_neg")
                value_fn = stat("pct", f"avg_{h}", p)
                s = _const(f"{self.percentile}th percentile {self.label_for(source)}",
                           faded_color(self.color(source), "000000", 0.6), fn, stats_key="pct")
                s["stats"] = {"pct": value_fn}
                series.append(s)
        return series, vnames


class UptimeGraph(Graph):
    kind = "uptime"

    def rrd_gen_graph(self) -> list[str]:
        g = self.rrd_options()
        sources = self.sources = self.rrd_get_sources()
        i = 0
        for tinstance in self.tinstances:
            for ds in self.data_sources:
                h = crc32hex(sources[i] if i < len(sources) else "")
                f = self.parse_filename(self.files[tinstance])
                g += [
                    f"DEF:avg_{h}={f}:{ds}:AVERAGE",
                    f"DEF:max_{h}={f}:{ds}:MAX",
                    f"CDEF:c_avg_{h}=avg_{h},86400,/",
                    f"CDEF:c_max_{h}=max_{h},86400,/",
                    f"VDEF:v_avg_{h}=c_avg_{h},AVERAGE",
                    f"VDEF:v_max_{h}=c_max_{h},MAXIMUM",
                ]
                i += 1
        g += StackedGraph.area_defs(self, sources, value="c_avg_{h}", op="+")
        for source in sources:
            g.append(f"AREA:area_{crc32hex(source)}#{faded_color(self.color(source))}")
        fmt = self.rrd_format
        for source in sources:
            h = crc32hex(source)
            g += [
                f"LINE1:area_{h}#{validate_color(self.color(source))}:{rrd_escape(self.legend_for(source))}",
                f"GPRINT:c_avg_{h}:LAST:{fmt} days\\l",
                f"LINE1:v_max_{h}#FF0000:Maximum:dashes",
                f"GPRINT:v_max_{h}:{fmt} days\\l",
                f"LINE1:v_avg_{h}#0000FF:Average:dashes",
                f"GPRINT:v_avg_{h}:{fmt} days\\l",
            ]
        return g

    def chart_plan(self, sources):
        series, vnames = [], []
        for source in sources:
            h = crc32hex(source)
            vnames += [f"area_{h}", f"c_avg_{h}", f"c_max_{h}"]
            series.append(_series(self.label_for(source), self.color(source), f"area_{h}",
                                  fill=faded_color(self.color(source)), hover=f"c_avg_{h}",
                                  stats={"last": stat("last", f"c_avg_{h}")}))
            series.append(_const("Maximum", "ff0000", stat("max", f"c_max_{h}"), stats_key="max"))
            series.append(_const("Average", "0000ff", stat("avg", f"c_avg_{h}"), stats_key="avg"))
        return series, vnames

    def chart(self, start, end, maxrows):
        chart = super().chart(start, end, maxrows)
        # the legend prints values with the format followed by " days"
        chart["format"]["suffix"] += " days"
        return chart


GRAPH_TYPES = {
    "default": DefaultGraph,
    "stacked": StackedGraph,
    "io": IOGraph,
    "uptime": UptimeGraph,
}


# --- titles and building graphs from request parameters ---

def default_title(args: dict) -> str:
    plugin, type_ = args.get("p") or "", args.get("t")
    title = plugin
    if type_ != args.get("p"):
        title += f" {type_ or ''}"
    if args.get("pi"):
        title += f" ({args['pi']})"
    if args.get("c"):
        title += f" ({args['c']})"
    return title


def _json_plugin(params: dict) -> tuple[str | None, dict]:
    """The plugin whose JSON describes the graph, and the parameters for titles."""
    params = dict(params)
    plugin = params.get("p")
    if plugin == "aggregation":
        parts = (params.get("pi") or "").split("-", 1)
        plugin = parts[0] if re.fullmatch(r"\w+", parts[0], re.UNICODE) else None
        params["pi"] = parts[1] if len(parts) > 1 and parts[1] else None
    return plugin, params


def graph_title(config: dict, plugins: Plugins, params: dict) -> str:
    """A graph's title without looking at its files, for pages and image alt text."""
    plugin, title_params = _json_plugin(params)
    spec = plugins.type_json(plugin, params.get("t"))
    if "title" in spec:
        return _title_template(spec["title"], title_params)
    return default_title(params)


def _title_template(template: str, params: dict) -> str:
    return (str(template).replace("{{PI}}", params.get("pi") or "")
            .replace("{{TI}}", params.get("ti") or "")
            .replace("{{HOST}}", params.get("h") or ""))


def build_graph(config: dict, plugins: Plugins, typesdb: TypesDB, params: dict) -> Graph | None:
    """CGP's graph.php: a Graph for the parameters, or None for unsupported types."""
    plugin, title_params = _json_plugin(params)
    type_ = params.get("t")
    spec = plugins.type_json(plugin, type_)
    kind = spec.get("type", "default")
    if kind == "none":
        return None
    graph = GRAPH_TYPES.get(kind, DefaultGraph)(config, params)

    # a graph without a plugin instance draws that type from every plugin instance
    if not title_params.get("pi") and plugins.merge_instances(plugin, type_):
        graph.merge_instances = True
        graph.rrd_files()

    data_sources = typesdb.data_sources(type_)
    if data_sources:
        graph.data_sources = data_sources
    # draw only some of the type's data sources
    if "data_sources" in spec:
        ds = spec["data_sources"]
        graph.data_sources = list(ds) if isinstance(ds, list) else [ds]

    if isinstance(spec.get("legend"), dict):
        graph.order = []
        for key, prop in spec["legend"].items():
            prop = prop if isinstance(prop, dict) else {}
            graph.order.append(key)
            graph.legend[key] = prop.get("name", key)
            if "color" in prop:
                graph.colors[key] = prop["color"]

    if "title" in spec:
        graph.rrd_title = _title_template(spec["title"], title_params)

    if "vertical" in spec:
        graph.rrd_vertical = str(spec["vertical"]).replace(
            "{{ND}}", config["network_datasize"][:1].upper() + config["network_datasize"][1:])

    if "rrdtool_opts" in spec:
        extra = spec["rrdtool_opts"]
        graph.rrdtool_opts += extra.split(" ") if isinstance(extra, str) else [str(o) for o in extra]

    if spec.get("datasize"):
        graph.scale = 8 if config["network_datasize"] == "bits" else 1

    if type_ == "if_octets":
        graph.percentile = config["percentile"]

    if "scale" in spec:
        graph.scale = plain_decimal(spec["scale"])

    if "base" in spec:
        graph.base = spec["base"]

    if "legend_format" in spec:
        graph.rrd_format = spec["legend_format"]

    return graph


def image_args(graph: Graph, fmt: str) -> list[str]:
    graph.prepare()
    return ["graph", "-", "-a", "SVG" if fmt == "svg" else "PNG", *graph.rrd_gen_graph()]


__all__ = [
    "Graph", "build_graph", "graph_title", "image_args", "crc32hex", "php_cmp", "Collectd", "TypesDB",
]
