"""Unit tests for the parts of CGP that don't need rrdtool.

The rrdtool arguments themselves are checked against the PHP CGP by
tests/parity.py, which needs both images running.
"""

import os

import pytest

from cgp import config as config_module
from cgp.app import params_from
from cgp.collectd import FILE_RE, Collectd, Plugins, TypesDB, php_cmp, php_sorted
from cgp.graphs import (
    build_graph, crc32hex, default_title, faded_color, graph_title, parse_format, plain_decimal,
    rrd_escape, y_limits,
)


@pytest.fixture
def config(tmp_path):
    cfg = config_module.load({"CGP_CONFIG": str(tmp_path / "none.toml")})
    cfg["datadir"] = str(tmp_path / "rrd")
    cfg["plugin_dir"] = str(tmp_path / "plugins")
    return cfg


def touch(root, *paths):
    for p in paths:
        path = os.path.join(root, p)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()


# --- PHP compatible ordering ---

def test_php_sort_compares_numeric_strings_as_numbers():
    assert php_sorted(["10", "2", "1", "NMI"]) == ["1", "2", "10", "NMI"]
    assert php_cmp("1.5", "1.25") == 1
    assert php_cmp("abc", "abd") == -1
    assert php_cmp(None, "") == 0


# --- collectd file names ---

@pytest.mark.parametrize("path, expected", [
    ("load/load.rrd", {"p": "load", "c": "", "pi": "", "t": "load", "ti": ""}),
    ("cpu-0/cpu-idle.rrd", {"p": "cpu", "c": "", "pi": "0", "t": "cpu", "ti": "idle"}),
    ("df-mnt-data/df_complex-free.rrd", {"p": "df", "c": "", "pi": "mnt-data", "t": "df_complex", "ti": "free"}),
    ("varnish-default-backend/total_requests-received.rrd",
     {"p": "varnish", "c": "default", "pi": "backend", "t": "total_requests", "ti": "received"}),
    ("smart-sda/smart_attribute-airflow-temperature-celsius.rrd",
     {"p": "smart", "c": "", "pi": "sda", "t": "smart_attribute", "ti": "airflow-temperature-celsius"}),
])
def test_file_names(path, expected):
    m = FILE_RE.search(path)
    assert {k: m.group(k) or "" for k in expected} == expected


def test_plugins_and_graph_grouping(config):
    touch(config["datadir"],
          "mc/load/load.rrd", "mc/cpu-0/cpu-idle.rrd", "mc/cpu-0/cpu-user.rrd", "mc/cpu-10/cpu-idle.rrd",
          "mc/cpu-2/cpu-idle.rrd", "mc/vmem/vmpage_io-memory.rrd", "mc/vmem/vmpage_io-swap.rrd",
          "mc/smart-sda/smart_temperature.rrd", "mc/smart-sdb/smart_temperature.rrd",
          "mc/stray.rrd", "notahost.txt")
    c = Collectd(config)
    plugins = Plugins(config)
    assert c.hosts() == ["mc"]
    assert c.plugins("mc") == ["cpu", "load", "smart", "vmem"]
    # type instances share a graph; plugin instances sort numerically
    assert [g["pi"] for g in c.graphs("mc", "cpu", plugins)] == ["0", "2", "10"]
    # vmem's vmpage_io keeps a graph per type instance
    assert [g["ti"] for g in c.graphs("mc", "vmem", plugins)] == ["memory", "swap"]
    # smart merges its disks into one graph per type
    assert c.graphs("mc", "smart", plugins) == [{"p": "smart", "c": "", "t": "smart_temperature", "ti": ""}]


def test_host_dir_stays_inside_datadir(config):
    touch(config["datadir"], "mc/load/load.rrd")
    c = Collectd(config)
    assert c.host_dir("mc")
    assert c.host_dir("..") is None
    assert c.host_dir(".") is None
    assert c.plugindata("..") == []


def test_local_plugin_json_overrides(config):
    os.makedirs(config["plugin_dir"])
    with open(os.path.join(config["plugin_dir"], "load.json"), "w") as f:
        f.write('{"load": {"title": "Load {{HOST}}", "legend": {"shortterm": {"color": "123456"}}}}')
    spec = Plugins(config).type_json("load", "load")
    assert spec["title"] == "Load {{HOST}}"
    # merged over CGP's own load.json, keeping its other settings
    assert spec["legend"]["shortterm"] == {"name": " 1 min", "color": "123456"}
    assert spec["legend_format"] == "%.2lf"


def test_typesdb_falls_back_to_cgps_copy(config):
    config["typesdb"] = ["/nonexistent/types.db"]
    types = TypesDB(config)
    assert types.data_sources("load") == ["shortterm", "midterm", "longterm"]
    assert types.data_sources("if_octets") == ["rx", "tx"]


# --- graphs ---

def test_graph_files_sources_and_colours(config):
    touch(config["datadir"], *[f"mc/cpu-0/cpu-{s}.rrd" for s in ("idle", "user", "system", "steal")])
    plugins, typesdb = Plugins(config), TypesDB(config)
    g = build_graph(config, plugins, typesdb, {"h": "mc", "p": "cpu", "pi": "0", "t": "cpu", "s": "86400"})
    assert g.kind == "stacked"
    assert g.rrd_title == "CPU-0 usage on mc"
    g.prepare()
    args = g.rrd_gen_graph()
    # the legend's order comes from cpu.json, then the rest
    assert g.sources == ["idle", "user", "system", "steal"]
    assert args[:4] == ["-u", "100", "-l", "0"]
    assert f"AREA:area_{crc32hex('idle')}#{faded_color('e8e8e8')}" in args
    assert any(a.startswith(f"LINE1:area_{crc32hex('user')}#0000ff:User  ") for a in args)


def test_merged_instances_name_lines_after_disks(config):
    touch(config["datadir"], "mc/smart-sda/smart_temperature.rrd", "mc/smart-sdb/smart_temperature.rrd")
    g = build_graph(config, Plugins(config), TypesDB(config), {"h": "mc", "p": "smart", "t": "smart_temperature"})
    assert g.merge_instances
    assert g.tinstances == ["sda", "sdb"]
    assert g.rrd_title == "Disk temperature on mc"


def test_unsupported_type_has_no_graph(config, tmp_path):
    os.makedirs(config["plugin_dir"])
    with open(os.path.join(config["plugin_dir"], "foo.json"), "w") as f:
        f.write('{"bar": {"type": "none"}}')
    assert build_graph(config, Plugins(config), TypesDB(config), {"h": "mc", "p": "foo", "t": "bar"}) is None


def test_titles():
    assert default_title({"p": "load", "t": "load"}) == "load"
    assert default_title({"p": "disk", "t": "disk_ops", "pi": "sda"}) == "disk disk_ops (sda)"


def test_aggregation_uses_the_aggregated_plugins_json(config):
    params = {"h": "mc", "p": "aggregation", "pi": "cpu-sum", "t": "cpu"}
    assert graph_title(config, Plugins(config), params) == "CPU-sum usage on mc"


def test_helpers():
    assert crc32hex("shortterm") == "9f24bee8"
    assert plain_decimal(0.0000115740740741) == "0.000011574074074"
    assert plain_decimal(8) == "8"
    assert rrd_escape("C:\\x:y") == "C\\:\\\\x\\:y"
    assert faded_color("0000ff") == "bfbfff"
    assert faded_color("zzz") == "bfbfbf"


@pytest.mark.parametrize("fmt, base, expected", [
    ("%5.1lf%s", None, {"decimals": 1, "si": True, "base": 1000, "prefix": "", "suffix": ""}),
    ("%5.1lf%sB", "1024", {"decimals": 1, "si": True, "base": 1024, "prefix": "", "suffix": "B"}),
    ("%5.1lf%%", None, {"decimals": 1, "si": False, "base": 1000, "prefix": "", "suffix": "%"}),
    ("%.2lf", None, {"decimals": 2, "si": False, "base": 1000, "prefix": "", "suffix": ""}),
])
def test_parse_format(fmt, base, expected):
    assert parse_format(fmt, base) == expected


def test_y_limits():
    assert y_limits(["-u", "100", "-l", "0"]) == {"min": 0.0, "max": 100.0, "rigid": False, "log": False}
    assert y_limits(["--rigid", "--lower-limit=5"])["min"] == 5.0


# --- settings and requests ---

def test_config_from_environment(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text('overview = ["load"]\n[time_range]\nload = 3600\n[cat]\nWeb = ["web1"]\n')
    cfg = config_module.load({"CGP_CONFIG": str(toml), "CGP_PERCENTILE": "95", "CGP_SHOWMEM": "false",
                              "CGP_DATADIR": "/srv/rrd/", "CGP_OVERVIEW": "load, cpu"})
    assert cfg["percentile"] == 95
    assert cfg["showmem"] is False
    assert cfg["datadir"] == "/srv/rrd"
    assert cfg["overview"] == ["load", "cpu"]
    assert cfg["time_range"] == {"default": 86400, "uptime": 31536000, "load": 3600}
    assert cfg["cat"] == {"Web": ["web1"]}
    assert cfg["max_width"] == 2000


def test_request_parameters_are_validated():
    assert params_from({"h": "mc", "p": "load", "t": "load", "s": "86400", "x": "500"}) == \
        {"h": "mc", "p": "load", "t": "load", "s": "86400", "x": "500"}
    assert params_from({"h": "../etc", "p": "lo/ad", "s": "1d", "pi": "sda 1", "junk": "x"}) == \
        {"pi": "sda 1"}
    assert params_from({"h": "mc;rm", "t": ""}) == {}
