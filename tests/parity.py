#!/usr/bin/env python3
"""Compares the Python CGP with the PHP CGP over the same RRD files.

Usage: tests/parity.py <php container> <python container>

Both containers must hold the same collectd tree (tests/make_rrd_tree.py),
and the PHP one a cmd.php: CGP's graph.php printing its rrdtool arguments as
JSON instead of drawing them. Checks, for every host and plugin page:

- the hosts, and each host's plugins in the same order;
- the same graphs, in the same order, with the same parameters;
- the same rrdtool arguments for every graph;
- the same load and memory figures on the index page.
"""

import html
import json
import re
import shlex
import subprocess
import sys
from urllib.parse import parse_qsl, urlencode

PHP, PY = sys.argv[1], sys.argv[2]
failures = 0


def get(container, path):
    out = subprocess.run(["docker", "exec", container, "curl", "-fsS", f"http://localhost{path}"],
                         capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"{container} {path}: {out.stderr.strip()}")
    return out.stdout


def check(name, php, py):
    global failures
    if php == py:
        return True
    failures += 1
    print(f"FAIL: {name}")
    if isinstance(php, list) and isinstance(py, list):
        for i, (a, b) in enumerate(zip(php, py)):
            if a != b:
                print(f"    first difference at {i}:\n      php: {a}\n      py:  {b}")
                break
        if len(php) != len(py):
            print(f"    lengths: php {len(php)}, py {len(py)}")
    else:
        print(f"    php: {php}\n    py:  {py}")
    return False


def params(url):
    return dict(parse_qsl(html.unescape(url).split("?", 1)[1]))


# --- hosts ---
php_index = get(PHP, "/")
py_index = get(PY, "/")
php_hosts = re.findall(r'<label><a href="/host\.php\?h=([^"]+)">', php_index)
py_hosts = re.findall(r'<a class="card host" href="/host/([^"]+)">', py_index)
check("hosts", php_hosts, py_hosts)

# --- index figures ---
for host in php_hosts:
    # the row ends with the last field's, hostinfo's and the row's </div>
    block = php_index.split(f'host.php?h={host}"', 1)[1].split("</div></div></div>", 1)[0] + "</div>"
    php_fields = re.findall(r'<div class="field([^"]*)">([^<]+)</div>', block)
    py_block = py_index.split(f'href="/host/{host}"', 1)[1].split("</a>", 1)[0]
    py_fields = re.findall(r'<span class="value ?([^"]*)">([^<]+)</span>', py_block)
    check(f"index figures for {host}",
          [(c.strip(), v) for c, v in php_fields], [(c.strip(), v) for c, v in py_fields])

graphs_checked = 0
for host in php_hosts:
    # --- plugins ---
    php_page = get(PHP, f"/host.php?h={host}")
    php_plugins = re.findall(r'<li><a [^>]*href="/host\.php\?h=[^&"]+&amp;p=(\w+)">', php_page)
    py_page = get(PY, f"/host/{host}")
    py_plugins = re.findall(rf'<li><a href="/host/{re.escape(host)}/(\w+)"', py_page)
    check(f"plugins of {host}", php_plugins, py_plugins)

    for plugin in [None] + php_plugins:
        php_page = get(PHP, f"/host.php?h={host}" + (f"&p={plugin}" if plugin else ""))
        py_page = get(PY, f"/host/{host}" + (f"/{plugin}" if plugin else ""))
        php_graphs = [params(u) for u in re.findall(r'<img src="(/graph\.php\?[^"]+)"', php_page)]
        py_graphs = [params(u) for u in re.findall(r'data-src="(/api/graph\?[^"]+)"', py_page)]
        if not check(f"graphs on {host}/{plugin or 'overview'}",
                     [sorted(g.items()) for g in php_graphs], [sorted(g.items()) for g in py_graphs]):
            continue

        # --- rrdtool arguments ---
        for g in php_graphs:
            q = urlencode(g)
            php_args = [str(a) for a in json.loads(get(PHP, f"/cmd.php?{q}"))]
            py_args = shlex.split(get(PY, f"/graph.txt?{q}").replace("\\\n", " "))[5:]
            check(f"rrdtool arguments for {q}", php_args, py_args)
            graphs_checked += 1

# --- detail size, aggregation and varnish graphs, which pages don't list ---
for q in ("h=mc&p=load&t=load&s=3600&x=1000&y=600",
          "h=mc&p=aggregation&pi=cpu-sum&t=cpu&s=86400",
          "h=mc&p=varnish&c=default&pi=backend&t=total_requests&s=86400",
          "h=mc&p=smart&pi=sda&t=smart_temperature&s=86400",
          "h=mc&p=interface&pi=enp3s0&t=if_octets&s=604800"):
    php_args = [str(a) for a in json.loads(get(PHP, f"/cmd.php?{q}"))]
    py_args = shlex.split(get(PY, f"/graph.txt?{q}").replace("\\\n", " "))[5:]
    check(f"rrdtool arguments for {q}", php_args, py_args)
    graphs_checked += 1

print(f"{graphs_checked} graphs compared, {failures} failure(s)")
sys.exit(1 if failures else 0)
