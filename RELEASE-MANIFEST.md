# GDSight release manifest

This repository pins the source dependencies and provenance sources for the
accepted-paper artifact as Git submodules. Clone with
`--recurse-submodules` to check out the exact revisions below.

| Path | Role | Source | Pinned commit |
| --- | --- | --- | --- |
| `external/datacrumbs` | tracer build | [izzet/datacrumbs](https://github.com/izzet/datacrumbs) | `f18b057c140fd5dab42d195742ba9479e45d8b6c` |
| `external/dfanalyzer` | offline attribution build | [izzet/dfanalyzer](https://github.com/izzet/dfanalyzer) | `6525f47ded068281750c37e03cec3d0dd23aee2f` |
| `external/dftracer` | trace-format provenance | [izzet/dftracer](https://github.com/izzet/dftracer) | `3c2f1d83673f388854ee77c358010dc273f5d596` |
| `external/nixl` | NIXL workload provenance | [ai-dynamo/nixl](https://github.com/ai-dynamo/nixl) | `77d79d88f958c371f6184fafdb03fa90022de995` |

DataCrumbs and DFAnalyzer are built from these checkouts. The NIXL workload
uses the `nixl-cu12` package, while trace parsing uses `dftracer-utils`; the
NIXL and DFTracer submodules preserve the corresponding source provenance.
