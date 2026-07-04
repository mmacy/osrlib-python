"""Old-School Essentials (B/X) rules engine for turn-based dungeon crawlers.

osrlib is the rules authority and game-state engine; the game supplies presentation,
input, and content. The library is headless and sans-I/O: it never renders, prompts,
sleeps, or touches the network, and all randomness flows through named deterministic
streams (see [`osrlib.core.rng`][osrlib.core.rng]).

Every symbol has exactly one import home: the kernel under `osrlib.core`, the crawl
framework under `osrlib.crawl`, and the shared services at the top level —
[`osrlib.data`][osrlib.data] (compiled SRD catalogs), [`osrlib.errors`][osrlib.errors]
(the typed exception hierarchy), [`osrlib.messages`][osrlib.messages] (message-code
formatting), [`osrlib.persistence`][osrlib.persistence] (saves and replay), and
[`osrlib.versioning`][osrlib.versioning] (schema and engine version stamping). The
package root re-exports nothing.
"""
