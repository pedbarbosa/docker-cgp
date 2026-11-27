#!/usr/bin/env python3
"""Creates a collectd-like RRD tree with two days of made-up data.

Usage: make_rrd_tree.py <datadir> [types.db]

It covers each graph type (default, stacked, io, uptime), merged plugin
instances (smart), a data_sources override (smart_attribute), numeric
instance sorting (irq), varnish categories, the aggregation plugin and the
plugins whose type instances keep their own graphs (vmem). Data is
deterministic, so two runs make the same files.
"""

import math
import os
import random
import subprocess
import sys
import time

STEP = 60
HOURS = 48
RRAS = [f"RRA:{cf}:0.1:{steps}:{rows}" for cf in ("AVERAGE", "MIN", "MAX")
        for steps, rows in ((1, 1440), (10, 1008), (60, 744), (1440, 400))]


def load_types(path):
    types = {}
    with open(path) as f:
        for line in f:
            parts = line.split(None, 1)
            if len(parts) != 2 or line.startswith("#"):
                continue
            types[parts[0]] = [ds.strip().split(":") for ds in parts[1].split(",")]
    return types


class Tree:
    def __init__(self, datadir, types, end):
        self.datadir = datadir
        self.types = types
        self.end = end - end % 300
        self.start = self.end - HOURS * 3600

    def rrd(self, rel, type_, gen):
        """Creates <datadir>/<rel>.rrd for a types.db type; gen(ds, t) gives a value."""
        path = os.path.join(self.datadir, rel + ".rrd")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        dss = self.types[type_]
        create = ["rrdtool", "create", path, "--start", str(self.start - STEP), "--step", str(STEP)]
        # samples are 300s apart, so the heartbeat must be longer than that
        create += [f"DS:{name}:{kind}:600:{lo}:{hi}" for name, kind, lo, hi in dss]
        subprocess.run(create + RRAS, check=True)
        counters = {name: 0.0 for name, *_ in dss}
        updates = []
        for t in range(self.start, self.end + 1, 300):
            values = []
            for name, kind, *_ in dss:
                v = gen(name, t)
                if kind in ("DERIVE", "COUNTER"):
                    counters[name] += max(v, 0) * 300
                    v = int(counters[name])
                values.append("U" if v is None else str(round(v, 4)) if kind == "GAUGE" else str(int(v)))
            updates.append(f"{t}:" + ":".join(values))
        for i in range(0, len(updates), 200):
            subprocess.run(["rrdtool", "update", path, *updates[i:i + 200]], check=True)


def wave(seed, base, amp, period=86400, noise=0.1):
    rnd = random.Random(seed)
    phase = rnd.random() * period

    def f(_ds, t):
        r = random.Random(f"{seed}-{t}-{_ds}")
        return max(base + amp * math.sin(2 * math.pi * (t + phase) / period) + base * noise * (r.random() - 0.5), 0)

    return f


def build(datadir, typesdb):
    types = load_types(typesdb)
    tree = Tree(datadir, types, int(time.time()))
    r = tree.rrd

    # host with every kind of graph
    h = "mc"
    r(f"{h}/load/load", "load", lambda ds, t: {"shortterm": 1.2, "midterm": 1.0, "longterm": 0.8}[ds]
      + wave("load", 0.6, 0.5)(ds, t))
    for cpu in ("0", "1", "2", "3"):
        for state, base in (("user", 12), ("system", 4), ("idle", 80), ("nice", 0.5), ("wait", 1.5),
                            ("interrupt", 0.2), ("softirq", 0.6), ("steal", 0.0)):
            r(f"{h}/cpu-{cpu}/cpu-{state}", "cpu", wave(f"cpu{cpu}{state}", base, base / 3))
    for name, base in (("used", 6e9), ("free", 2e9), ("buffered", 3e8), ("cached", 7e9),
                       ("slab_recl", 4e8), ("slab_unrecl", 1e8)):
        r(f"{h}/memory/memory-{name}", "memory", wave(f"mem{name}", base, base / 10))
    for name, base in (("used", 2e8), ("free", 8e9), ("cached", 1e7)):
        r(f"{h}/swap/swap-{name}", "swap", wave(f"swap{name}", base, base / 20))
    r(f"{h}/swap/swap_io-in", "swap_io", wave("swapin", 50, 40))
    r(f"{h}/swap/swap_io-out", "swap_io", wave("swapout", 30, 20))
    for fs, used in (("root", 4e10), ("mnt-data", 3e12)):
        for name, base in (("used", used), ("free", used / 2), ("reserved", used / 20)):
            r(f"{h}/df-{fs}/df_complex-{name}", "df_complex", wave(f"df{fs}{name}", base, base / 100, noise=0))
    for disk in ("sda", "nvme0n1"):
        r(f"{h}/disk-{disk}/disk_octets", "disk_octets", wave(f"do{disk}", 4e6, 3e6, 7200))
        r(f"{h}/disk-{disk}/disk_ops", "disk_ops", wave(f"dop{disk}", 120, 90, 7200))
        r(f"{h}/disk-{disk}/disk_time", "disk_time", wave(f"dt{disk}", 4, 3, 7200))
        r(f"{h}/disk-{disk}/disk_merged", "disk_merged", wave(f"dm{disk}", 10, 8, 7200))
        r(f"{h}/disk-{disk}/disk_io_time", "disk_io_time", wave(f"dio{disk}", 200, 150, 7200))
        r(f"{h}/disk-{disk}/pending_operations", "pending_operations", wave(f"po{disk}", 2, 2, 7200))
    for iface in ("enp3s0", "docker0"):
        r(f"{h}/interface-{iface}/if_octets", "if_octets", wave(f"io{iface}", 2e6, 1.5e6, 3600))
        r(f"{h}/interface-{iface}/if_packets", "if_packets", wave(f"ip{iface}", 2000, 1500, 3600))
        r(f"{h}/interface-{iface}/if_errors", "if_errors", wave(f"ie{iface}", 0.01, 0.01, 3600))
        r(f"{h}/interface-{iface}/if_dropped", "if_dropped", wave(f"id{iface}", 0.2, 0.2, 3600))
    r(f"{h}/uptime/uptime", "uptime", lambda ds, t: t - (tree.start - 20 * 86400))
    for disk, temp in (("sda", 34), ("sdb", 38), ("nvme0n1", 45)):
        r(f"{h}/smart-{disk}/smart_temperature", "smart_temperature", wave(f"st{disk}", temp, 3))
        r(f"{h}/smart-{disk}/smart_poweron", "smart_poweron", lambda ds, t: t - 1.5e8)
        r(f"{h}/smart-{disk}/smart_powercycles", "smart_powercycles", lambda ds, t: 812)
        r(f"{h}/smart-{disk}/smart_badsectors", "smart_badsectors", lambda ds, t: 0)
        for attr in ("airflow-temperature-celsius", "attribute-169", "command-timeout"):
            r(f"{h}/smart-{disk}/smart_attribute-{attr}", "smart_attribute",
              lambda ds, t, a=attr: {"current": 100, "worst": 95, "threshold": 10, "pretty": 33 + len(a)}[ds])
    for state, base in (("running", 2), ("sleeping", 300), ("zombies", 0), ("blocked", 0.2),
                        ("stopped", 0), ("paging", 0)):
        r(f"{h}/processes/ps_state-{state}", "ps_state", wave(f"ps{state}", base, base / 5))
    r(f"{h}/processes/fork_rate", "fork_rate", wave("fork", 8, 4))
    r(f"{h}/processes-dockerd/ps_rss", "ps_rss", wave("rss", 9e7, 1e7))
    r(f"{h}/processes-dockerd/ps_cputime", "ps_cputime", wave("cput", 2e4, 1e4))
    r(f"{h}/processes-dockerd/ps_count", "ps_count", lambda ds, t: {"processes": 1, "threads": 40}[ds])
    r(f"{h}/processes-dockerd/io_ops", "io_ops", wave("ioops", 30, 20))
    r(f"{h}/sensors-coretemp-isa-0000/temperature-temp1", "temperature", wave("temp1", 48, 6))
    r(f"{h}/sensors-coretemp-isa-0000/temperature-temp2", "temperature", wave("temp2", 46, 6))
    for irq in ("1", "2", "10", "16", "NMI"):
        r(f"{h}/irq/irq-{irq}", "irq", wave(f"irq{irq}", 20, 10))
    r(f"{h}/entropy/entropy", "entropy", wave("entropy", 3500, 300))
    r(f"{h}/users/users", "users", lambda ds, t: 2)
    r(f"{h}/contextswitch/contextswitch", "contextswitch", wave("cs", 4000, 1500))
    r(f"{h}/vmem/vmpage_io-memory", "vmpage_io", wave("vmmem", 300, 200))
    r(f"{h}/vmem/vmpage_io-swap", "vmpage_io", wave("vmswap", 5, 4))
    r(f"{h}/aggregation-cpu-sum/cpu-user", "cpu", wave("aggu", 50, 20))
    r(f"{h}/aggregation-cpu-sum/cpu-system", "cpu", wave("aggs", 15, 5))
    r(f"{h}/varnish-default-backend/total_requests-received", "total_requests", wave("varn", 30, 10))

    # smaller hosts, for the index page
    for host, cpus in (("web1", 1), ("mail2", 2)):
        r(f"{host}/load/load", "load", wave(f"{host}load", 2.5 * cpus, 1))
        for cpu in range(cpus):
            for state, base in (("user", 20), ("idle", 75)):
                r(f"{host}/cpu-{cpu}/cpu-{state}", "cpu", wave(f"{host}{cpu}{state}", base, 5))
        for name, base in (("used", 3.5e9), ("free", 2e8), ("buffered", 1e8), ("cached", 2e8)):
            r(f"{host}/memory/memory-{name}", "memory", wave(f"{host}{name}", base, base / 20))


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "/app/cgp/data/types.db")
