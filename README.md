# cgp (Python)

## CollectD Graph Panel (CGP), rewritten in Python

This `python` branch holds a Python rewrite of [CGP](https://github.com/pedbarbosa/CGP), the web front-end for the RRD files [collectd](https://collectd.org/) writes. It was ported from CGP's `dev` branch and is published to Docker Hub as [`pedbarbosa/cgp:python`](https://hub.docker.com/r/pedbarbosa/cgp/tags). The PHP image stays on `main` as `pedbarbosa/cgp:latest` and `:dev`.

It reads the same files and plugin definitions and draws the same graphs as the PHP CGP. Graphs are interactive and drawn in the browser, and rrdtool PNG and SVG images are still available for every graph.

### Running it

```yaml
services:
  cgp:
    image: pedbarbosa/cgp:python
    container_name: cgp
    volumes:
      - /etc/localtime:/etc/localtime:ro
      - /var/lib/collectd/rrd:/var/lib/collectd/rrd:ro
      # optional: settings and extra plugin JSON files, see below
      # - ./cgp:/etc/cgp:ro
    ports:
      - 8085:80
    restart: unless-stopped
```

It is a drop-in replacement for the PHP image: it listens on port 80, reads `/var/lib/collectd/rrd`, and works with the same `curl` healthcheck. It runs as an unprivileged user (uid 10001) with gunicorn, and has its own `HEALTHCHECK`.

### What's new compared with the PHP CGP

- **Interactive graphs** ([uPlot](https://github.com/leeoniya/uPlot), served from the image with no CDN):
  - Hover to read every line's value at that time. The crosshair follows across all graphs on the page.
  - Drag to zoom in: it loads finer data for the range. Double-click to go back.
  - On the detail page you can move earlier or later, zoom out, and share the zoomed range: it is kept in the address.
- **A legend table** under each graph with Min, Avg, Max, Last (and Total for I/O graphs), computed as rrdtool's legend does. Click a row to hide or show that line.
- **Light and dark themes** that follow the system, with a toggle in the top bar.
- **Live mode**: graphs reload every minute, except zoomed ones. On the index page, the whole page reloads.
- **Filter box** on host pages, e.g. `temperature` on the SMART page.
- **Lazy loading**: graphs load as they scroll into view, so long pages (SMART, disks) open quickly.
- **Responsive layout**: on phones, the plugin list becomes a scrolling bar and graphs take the full width.
- **Index cards** showing load (coloured by CPU count), memory used with a bar, and time since the last update. Memory and last update are now on by default.
- A **PNG download** for every graph, and **PNG and SVG** downloads at detail size.

### Feature comparison with the PHP CGP

Checked against CGP `dev` (5b72ff2). `tests/compare-with-php` runs the comparison: same data in both, same hosts, plugins and graphs on every page in the same order, and **identical rrdtool arguments** for every graph.

| PHP CGP | Python CGP |
|---|---|
| Index: hosts with 1/5/15-minute load, warn/crit by CPU count (`showload`) | Same |
| Memory used % (`showmem`) | Same, with a bar; on by default |
| Last update, with jQuery timeago (`showtime`) | Same, without jQuery; on by default |
| Host categories by list or regular expression (`cat`) | Same: regular expressions in Python syntax; `/…/` delimiters still accepted |
| "No collectd hosts found" warning | Same |
| Host overview of the `overview` plugins, and `overview_filter` | Same |
| Plugin list: overview plugins first, then the rest | Same; a scrolling bar instead of the hamburger menu on phones |
| Time ranges (`term`), kept when switching plugins | Same, as a segmented control |
| Default time range per plugin (`time_range`) | Same |
| Detail page with a large graph | Same, plus zoom, pan and a shareable range |
| Graph types `default`, `stacked`, `io`, `uptime`, `none` | Same rrdtool arguments; in the browser: lines, stacked areas, mirrored I/O, and uptime with max/avg lines |
| Plugin JSON: titles, legends, colours, `rrdtool_opts`, `scale`, `base`, `datasize`, `legend_format`, `vertical` | Same files, same results |
| `merge_instances`, `data_sources`, `plugin/local/` overrides | Same, plus a mountable `/etc/cgp/plugin/` |
| `percentile`, `negative_io`, `graph_minmax`, `graph_smooth`, `network_datasize`, `rrdtool_opts` | Same |
| `aggregation` plugin, `varnish` categories, libvirt/snmp/vmem grouping | Same |
| `graph_type` `png` / `svg` | Same; the default is now `interactive` |
| `graph_type` `canvas` / `hybrid` (jsrrdgraph) | Replaced by interactive charts |
| `rrd.php`, `rrd_url`, `rrd_fetch_method` (raw RRD files for canvas) | Dropped |
| Flushing collectd or rrdcached first (`socket`, `flush_type`) | Same |
| Browser caching of graphs (`cache`) | Same (`Cache-Control` and `Expires`) |
| Graph sizes and limits (`width`, `detail-width`, `max-width`, …) | Same, spelled `detail_width`, `max_width`, … |
| `page_refresh` meta refresh | Dropped; use the Live toggle |
| collectd 4 compatibility (`version`) | Dropped |
| `conf/config.local.php` | A TOML file and environment variables, see below |
| Debug output of the rrdtool command | `/graph.txt?…` |
| Request validation, error image for bad requests | Same rules; host names must also be directories inside the datadir |
| Footer with CGP's version and licence | The image's docker-cgp commit and the GPLv3 licence |
| `index.php`, `host.php`, `detail.php`, `graph.php` URLs | New URLs, below; the old ones aren't kept |
| Graph times in the server's timezone (`default_timezone`) | rrdtool images: same. Interactive graphs: the browser's timezone |

### Pages and URLs

| URL | What it is |
|---|---|
| `/` | Hosts |
| `/host/<host>` | A host's overview |
| `/host/<host>/<plugin>` | All graphs of a plugin |
| `/host/<host>/<plugin>/<type>?pi=…&ti=…` | One graph, large (`start` and `end` keep a zoomed range) |
| `/graph.png?h=…&p=…&pi=…&t=…&ti=…&s=…&x=…&y=…` | rrdtool PNG, as CGP's `graph.php` |
| `/graph.svg?…` | rrdtool SVG |
| `/graph.txt?…` | The rrdtool command for a graph |
| `/api/graph?…&w=…&start=…&end=…` | A graph's data and legend as JSON |

Parameters keep CGP's names: `h` host, `p` plugin, `pi` plugin instance, `c` category, `t` type, `ti` type instance, `s` seconds.

### Settings

Settings have the names and defaults of CGP's `conf/config.php` (dashes became underscores). Change them in `/etc/cgp/config.toml` (or the path in `CGP_CONFIG`), or with environment variables named `CGP_` plus the setting's name in capitals. Lists in environment variables are comma-separated.

```toml
overview = ["load", "cpu", "memory", "swap", "interface"]
percentile = 95
network_datasize = "bits"
graph_type = "interactive"    # or "png", "svg"
socket = "unix:///var/run/collectd-unixsock"

[time_range]
default = 86400
uptime = 31536000

[overview_filter.interface]
pi = "eth0"
t = "if_octets"

[cat]
Mailservers = 'mail\d+'
Web = ["web1", "web2"]
```

```yaml
    environment:
      CGP_PERCENTILE: "95"
      CGP_OVERVIEW: load,cpu,memory,interface
```

Plugin JSON files in `/etc/cgp/plugin/` (`plugin_dir`) are merged over CGP's own, as `plugin/local/` was in the PHP CGP. `CGP_WORKERS` and `CGP_THREADS` set gunicorn's processes (2) and threads per process (8), and `PORT` its port (80).

### Building and testing

- `./rebuild` builds `pedbarbosa/cgp:python` locally and runs `./test` on it; `./rebuild --compare` also compares it with the PHP CGP. It doesn't publish: unlike `main`'s `rebuild`, it doesn't use docker-image-refresh, which would tag the image `latest`.
- `./test <image>` starts the image and checks it lists and graphs collectd data.
- `python -m pytest` runs the unit tests (`pip install -r requirements.txt pytest` first).
- `tests/compare-with-php <python image> [php image]` makes two days of collectd data (`tests/make_rrd_tree.py`) in both images and compares every page and graph (`tests/parity.py`).

The [Publish python image](.github/workflows/publish-python.yml) workflow runs on every push to `python` (except README and compose changes):
1. Runs the unit tests.
2. Builds the image, runs `./test`, and pushes `pedbarbosa/cgp:python`.
3. Compares the image with `pedbarbosa/cgp:dev`. A CGP change that isn't ported yet fails the run without holding up the image.

Failures go to Slack, as on `main`. The workflow uses the `DOCKERHUB_TOKEN` and `SLACK_WEBHOOK_URL` repository secrets.

`requirements.txt` is compiled from `pyproject.toml` with `pip-compile --strip-extras`.

### Licence

CGP is distributed under the [GNU General Public License v3](LICENSE), and so is this rewrite, which includes CGP's plugin definitions and `types.db`. CGP was written by Pim van den Berg with contributions from many others. uPlot is MIT-licensed ([licence](cgp/static/vendor/uPlot.LICENSE)).
