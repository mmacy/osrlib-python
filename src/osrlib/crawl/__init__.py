"""The crawl framework layer: the dungeon crawl game loop.

`osrlib.crawl` implements the dungeon adventuring procedures on top of the
`osrlib.core` kernel: the adventure container with its base town, the multi-level
dungeon grid with keyed areas, the crawl party with marching order, the exploration
turn loop, the encounter procedure, the range-track battle state machine, and the
`GameSession` command/event API. The kernel never imports from this package —
layering is a spec invariant.
"""
