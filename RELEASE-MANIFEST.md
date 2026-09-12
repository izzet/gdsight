# GDSight release manifest

This repository pins every external source dependency as a Git submodule.
Clone with `--recurse-submodules` to check out the exact revisions below.
The commit recorded for each path is the revision included by this release;
the URL is the configured submodule source.

| Path | Source | Pinned commit |
| --- | --- | --- |
| `external/brahma` | [izzet/brahma](https://github.com/izzet/brahma) | `530126529c274df1e44f0477dea8003ef7b0165a` |
| `external/cucim` | [rapidsai/cucim](https://github.com/rapidsai/cucim) | `a2985ec6e533f8836a5b751a3a5b64c7dc7ea867` |
| `external/datacrumbs` | [izzet/datacrumbs](https://github.com/izzet/datacrumbs) | `f18b057c140fd5dab42d195742ba9479e45d8b6c` |
| `external/deepspeedexamples` | [deepspeedai/DeepSpeedExamples](https://github.com/deepspeedai/DeepSpeedExamples) | `6eb258264dda7e4164c87be88df28b721d9d9e7b` |
| `external/dfanalyzer` | [izzet/dfanalyzer](https://github.com/izzet/dfanalyzer) | `6525f47ded068281750c37e03cec3d0dd23aee2f` |
| `external/dftracer` | [izzet/dftracer](https://github.com/izzet/dftracer) | `3c2f1d83673f388854ee77c358010dc273f5d596` |
| `external/elbencho` | [breuner/elbencho](https://github.com/breuner/elbencho) | `139d7184b5e3efdbfd019f2be6bb23b17e558b39` |
| `external/espn` | [susavlsh10/ESPN-v1](https://github.com/susavlsh10/ESPN-v1) | `66ed0bec756e689ecd00f32c2cd1d4ed6a506231` |
| `external/nixl` | [ai-dynamo/nixl](https://github.com/ai-dynamo/nixl) | `77d79d88f958c371f6184fafdb03fa90022de995` |
| `external/zns-tools` | [stonet-research/zns-tools](https://github.com/stonet-research/zns-tools) | `e0b12a8bbc30d12797d0ef1d24b4ed4840a745bc` |

The three GDSight-maintained forks are DataCrumbs, DFAnalyzer, and DFTracer.
Their pinned revisions are public release requirements alongside this repository.
