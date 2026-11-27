Put your local plugins (or modified versions of CGP plugins) here.

A type in a plugin's json can also set:

- `merge_instances`: `true` draws one graph per type (and type instance)
  with a line per plugin instance, instead of a graph per plugin instance.
  `smart.json` uses it for one graph per metric across all disks.
- `data_sources`: the data sources to draw, e.g. `["pretty"]`, instead of
  every data source of the type in types.db.
