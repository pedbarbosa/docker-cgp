"""The web app: pages, graph images and chart data."""

from __future__ import annotations

import base64
import logging
import os
import re
import time
from urllib.parse import urlencode

from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

from . import config as config_module
from . import rrd
from .collectd import Collectd, Plugins, TypesDB
from .config import DATA_DIR, PACKAGE_DIR
from .graphs import build_graph, graph_title, image_args

log = logging.getLogger("cgp")

# 1x1 transparent PNG, for types marked as unsupported ("type": "none")
BLANK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")

# Request parameters, validated as CGP's GET() does
PARAM_RULES = {
    "h": ("host", re.compile(r"^[\w\-.: ]+$")),
    "pi": ("plugin instance", re.compile(r"^[\w\-.: ]+$")),
    "ti": ("type instance", re.compile(r"^[\w\-.: ]+$")),
    "p": ("plugin", re.compile(r"^\w+$")),
    "c": ("category", re.compile(r"^\w+$")),
    "t": ("type", re.compile(r"^\w+$")),
    "s": ("seconds", None),
    "x": ("x-axis", None),
    "y": ("y-axis", None),
    "start": ("start", None),
    "end": ("end", None),
    "w": ("width", None),
}
GRAPH_KEYS = ("p", "c", "pi", "t", "ti")


def _numeric(value: str) -> bool:
    return bool(re.fullmatch(r"\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*", value))


def params_from(args) -> dict:
    """The valid, non-empty parameters; invalid ones are logged and dropped."""
    out = {}
    for key, (desc, pattern) in PARAM_RULES.items():
        value = args.get(key)
        if value is None or value == "":
            continue
        ok = pattern.match(value) if pattern else _numeric(value)
        if not ok:
            log.warning('Invalid %s in request["%s"]: "%s"', desc, key, value)
            continue
        out[key] = value
    return out


def create_app(config: dict | None = None) -> Flask:
    config = config or config_module.load()
    app = Flask(__name__, static_folder=str(PACKAGE_DIR / "static"), template_folder=str(PACKAGE_DIR / "templates"))
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600
    collectd = Collectd(config)
    plugins = Plugins(config)
    typesdb = TypesDB(config)
    revision = (os.environ.get("CGP_REVISION") or "dev").strip()

    # --- helpers for templates ---

    def time_range(plugin: str | None) -> int:
        tr = config["time_range"]
        return int(tr.get(plugin, tr["default"])) if plugin else int(tr["default"])

    def query(params: dict, **extra) -> str:
        merged = {**params, **extra}
        return urlencode({k: v for k, v in merged.items() if v is not None and v != ""})

    def graph_params(host: str, item: dict, seconds) -> dict:
        p = {k: item.get(k) for k in GRAPH_KEYS if item.get(k)}
        p["h"] = host
        p["s"] = seconds
        return p

    def detail_url(params: dict) -> str:
        rest = {k: v for k, v in params.items() if k not in ("h", "p", "t")}
        url = f"/host/{params['h']}/{params['p']}/{params.get('t') or params['p']}"
        q = query(rest)
        return f"{url}?{q}" if q else url

    def plugin_nav(host: str, host_plugins: list[str]) -> list[str]:
        """The overview's plugins first, in its order, then the others."""
        first = [p for p in config["overview"] if p in host_plugins]
        return first + [p for p in host_plugins if p not in first]

    def graphs_for(host: str, plugin: str, overview: bool, seconds) -> list[dict]:
        graphs = []
        for item in collectd.graphs(host, plugin, plugins):
            flt = config["overview_filter"].get(plugin)
            if overview and isinstance(flt, dict) and any(item.get(k) != v for k, v in flt.items()):
                continue
            # types marked as unsupported are skipped
            if plugins.graph_type(plugin, item.get("t")) == "none":
                continue
            params = graph_params(host, item, seconds if seconds else time_range(plugin))
            graphs.append({
                "params": params,
                "title": graph_title(config, plugins, params),
                "query": query(params),
                "detail": detail_url(params),
            })
        return graphs

    def breadcrumbs(params: dict) -> list[str]:
        crumbs = []
        if params.get("h"):
            crumbs.append(params["h"][:1].upper() + params["h"][1:])
        if params.get("p"):
            crumbs.append(params["p"][:1].upper() + params["p"][1:])
        if params.get("pi"):
            crumbs.append(params["pi"])
        if params.get("t") and params.get("p") and params["t"] != params["p"]:
            crumbs.append(params["t"])
        if params.get("ti"):
            crumbs.append(params["ti"])
        return crumbs

    @app.context_processor
    def globals_():
        return {
            "config": config,
            "revision": revision,
            "short_revision": revision[:7] if re.fullmatch(r"[0-9a-f]{40}", revision) else revision,
            "query": query,
        }

    # --- index ---

    def host_summary(host: str) -> dict:
        hostdir = os.path.join(config["datadir"], host)
        summary: dict = {"host": host, "load": [], "mem": None, "updated": None}
        load_file = os.path.join(hostdir, "load", "load.rrd")
        load_info = None
        if config["showload"] or config["showtime"]:
            graph = build_graph(config, plugins, typesdb, {"h": host, "p": "load", "t": "load"})
            if graph:
                rrd.flush(config, list(graph.identifiers.values()))
            load_info = rrd.info(config, load_file)
        if config["showload"] and load_info:
            values = [rrd.info_float(load_info, f"ds[{ds}].last_ds") for ds in ("shortterm", "midterm", "longterm")]
            if f"ds[shortterm].last_ds" in load_info:
                cores = len(collectd.group_plugindata(collectd.plugindata(host, "cpu"), plugins))
                for v in values:
                    level = ""
                    if v is not None and cores > 0:
                        level = "crit" if v > cores * 2 else "warn" if v > cores else ""
                    summary["load"].append({"value": v, "level": level})
                summary["cores"] = cores
        if config["showmem"]:
            infos = {name: rrd.info(config, os.path.join(hostdir, "memory", f"memory-{name}.rrd"))
                     for name in ("used", "free", "buffered", "cached")}
            if all(infos.values()):
                v = {k: rrd.info_float(i, "ds[value].last_ds") for k, i in infos.items()}
                total = sum(x for x in v.values() if x is not None)
                if None not in v.values() and total > 0:
                    percent = v["used"] * 100 / total
                    summary["mem"] = {"value": int(percent),
                                      "level": "crit" if percent > 90 else "warn" if percent > 70 else ""}
        if config["showtime"] and load_info and "last_update" in load_info:
            last = int(float(load_info["last_update"]))
            age = int(time.time()) - last
            summary["updated"] = {"epoch": last, "age": age,
                                  "level": "crit" if age > 300 else "warn" if age > 60 else "",
                                  "iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(last))}
        return summary

    def categories(hosts: list[str]) -> list[tuple[str, list[str]]]:
        out, shown = [], []
        for name, members in (config.get("cat") or {}).items():
            if isinstance(members, str):
                m = re.fullmatch(r"/(.*)/([a-z]*)", members, re.S)
                pattern = re.compile(m.group(1), re.I if m and "i" in m.group(2) else 0) if m else re.compile(members)
                members = [h for h in hosts if pattern.search(h)]
            members = [str(h) for h in members]
            out.append((str(name), members))
            shown += members
        rest = [h for h in hosts if h not in shown]
        if rest:
            out.append(("uncategorized", rest))
        return out

    @app.route("/")
    def index():
        hosts = collectd.hosts()
        groups = [(name, [host_summary(h) for h in members]) for name, members in categories(hosts)]
        return render_template("index.html", groups=groups, no_hosts=not hosts, crumbs=[])

    # --- host pages ---

    def host_page(host: str, plugin: str | None, type_: str | None = None):
        params = params_from(request.args)
        if not re.fullmatch(PARAM_RULES["h"][1], host) or (plugin and not re.fullmatch(r"\w+", plugin)) \
                or (type_ and not re.fullmatch(r"\w+", type_)):
            abort(404, "Unknown host")
        host_plugins = collectd.plugins(host) if collectd.host_dir(host) else []
        if not host_plugins:
            abort(404, "Unknown host")
        params["h"] = host
        if plugin:
            params["p"] = plugin
        if type_:
            params["t"] = type_
        seconds = params.get("s")
        context = {
            "host": host,
            "plugin": plugin,
            "nav": plugin_nav(host, host_plugins),
            "seconds": seconds,
            "crumbs": breadcrumbs(params),
            "terms": [(name, int(s)) for name, s in config["term"].items()],
        }
        if type_:
            graph = {k: params[k] for k in ("h", "p", "pi", "c", "t", "ti") if k in params}
            graph["s"] = seconds or time_range(plugin)
            selected = int(float(graph["s"]))
            return render_template(
                "detail.html", **context,
                graph=graph, title=graph_title(config, plugins, graph), query_=query(graph),
                selected=selected, start=params.get("start"), end=params.get("end"),
                range_base={k: v for k, v in graph.items() if k != "s"},
            )
        selected_plugins = [plugin] if plugin else [p for p in config["overview"] if p in host_plugins]
        sections = [{"plugin": p, "graphs": graphs_for(host, p, not plugin, seconds)} for p in selected_plugins]
        selected = int(float(seconds)) if seconds else time_range(plugin)
        return render_template("host.html", **context, sections=sections, selected=selected,
                               overview=not plugin)

    @app.route("/host/<host>")
    def host_overview(host):
        return host_page(host, None)

    @app.route("/host/<host>/<plugin>")
    def host_plugin(host, plugin):
        return host_page(host, plugin)

    @app.route("/host/<host>/<plugin>/<type_>")
    def detail(host, plugin, type_):
        return host_page(host, plugin, type_)

    # --- graphs ---

    def graph_request(width_key="x", height_key="y"):
        params = params_from(request.args)
        if not params.get("h"):
            return params, None, "missing host"
        for key, limit in ((width_key, "max_width"), (height_key, "max_height")):
            if key in params:
                v = float(params[key])
                if not v.is_integer() or not 10 <= v <= config[limit]:
                    log.warning('Invalid image dimension, %s="%s"', key, params[key])
                    return params, None, "invalid dimension"
                params[key] = int(v)
        return params, build_graph(config, plugins, typesdb, params), None

    def error_image():
        return send_file(DATA_DIR / "error.png", mimetype="image/png", max_age=0), 400

    def cache_headers(resp: Response) -> Response:
        if isinstance(config["cache"], (int, float)) and config["cache"] > 0:
            resp.cache_control.max_age = int(config["cache"])
            resp.expires = int(time.time() + config["cache"])
        return resp

    def image(fmt: str):
        params, graph, error = graph_request()
        if error:
            return error_image()
        if graph is None:
            return Response(BLANK_PNG, mimetype="image/png")
        if not graph.files:
            log.error('CGP: no RRD files found for "%s/%s"', params.get("h", ""), params.get("p", ""))
            return error_image()
        try:
            data = rrd.run(config, image_args(graph, fmt))
        except rrd.RRDError as e:
            log.error('CGP: %s for "%s/%s"', e, params.get("h", ""), params.get("p", ""))
            return error_image()
        if not data or (fmt == "png" and not data.startswith(b"\x89PNG")):
            log.error('CGP: rrdtool exited 0 without an image for "%s/%s"', params.get("h", ""), params.get("p", ""))
            return error_image()
        mimetype = "image/svg+xml" if fmt == "svg" else "image/png"
        resp = Response(data, mimetype=mimetype)
        filename = re.sub(r"[^\w .()-]", "_", graph.rrd_title, flags=re.ASCII).strip() or "graph"
        resp.headers["Content-Disposition"] = f'inline; filename="{filename}.{fmt}"'
        return cache_headers(resp)

    @app.route("/graph.png")
    def graph_png():
        return image("png")

    @app.route("/graph.svg")
    def graph_svg():
        return image("svg")

    @app.route("/graph.txt")
    def graph_txt():
        """The rrdtool command for a graph, one argument per line, for debugging."""
        import shlex
        params, graph, error = graph_request()
        if error or graph is None:
            return Response(error or "unsupported type\n", mimetype="text/plain", status=400 if error else 200)
        args = image_args(graph, "png")
        text = " \\\n".join(shlex.quote(a) for a in [config["rrdtool"], *args]) + "\n"
        return Response(text, mimetype="text/plain")

    @app.route("/api/graph")
    def graph_json():
        params, graph, error = graph_request()
        if error:
            return jsonify(error=error), 400
        if graph is None:
            return jsonify(none=True)
        if not graph.files:
            log.error('CGP: no RRD files found for "%s/%s"', params.get("h", ""), params.get("p", ""))
            return jsonify(error="No RRD files found", title=graph.rrd_title), 404
        width = int(float(params.get("w", config["width"])))
        width = max(10, min(width, 4000))
        if "start" in params and "end" in params and float(params["start"]) < float(params["end"]):
            start, end = str(int(float(params["start"]))), str(int(float(params["end"])))
        else:
            seconds = int(float(params["s"])) if "s" in params else 86400
            start, end = f"e-{seconds}", "now"
        try:
            chart = graph.chart(start, end, width)
        except rrd.RRDError as e:
            log.error('CGP: %s for "%s/%s"', e, params.get("h", ""), params.get("p", ""))
            return jsonify(error="rrdtool failed", title=graph.rrd_title), 500
        chart["png"] = "/graph.png?" + query({k: v for k, v in params.items() if k not in ("w", "start", "end")})
        return cache_headers(jsonify(chart))

    @app.route("/LICENSE")
    def license_():
        return send_file(PACKAGE_DIR.parent / "LICENSE", mimetype="text/plain")

    @app.errorhandler(HTTPException)
    def http_error(e: HTTPException):
        if request.path.startswith("/api/"):
            return jsonify(error=e.description), e.code
        return render_template("error.html", code=e.code, message=e.description, crumbs=[]), e.code

    @app.after_request
    def security_headers(resp: Response) -> Response:
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'")
        return resp

    return app
