# Phase 17 plan: passages

Phase 17 of [the osrlib spec](spec.md) adds the passage, a transition the party crosses by walking across one side of its cell with `MoveParty`. osr-forge's map tracing writes passages where two levels' floors meet on a printed map, and re-pod writes each one as a FRUA transfer. The cross-project design is the "Passages" section of re-pod's `docs/plans/map-tracing.md`, and passages ship in osrlib 2.0.

## Chunks

The phase was one chunk, `passage`, built under the suite's working method.

- **Contract**, pull requests 153 and 154: the `"passage"` kind and the `side` field on `TransitionSpec`, the `LevelSpec.passage_at` stub, the spec's passage bullet and roadmap entry, and `tests/test_passages.py`. Pull request 154 added a test that a passage may land on another level's cell with its own coordinates, and on a cell whose coordinates match a far cell on a different level.
- **Implementation**: the model's side rule, `passage_at`, `transition_at` and `UseStairs` ignoring passages, the passage branch of `MoveParty`, the player view's open side, and the `validate_adventure` refusals.

## What the contract pins

- **Event order.** A crossing to another level emits the level or dungeon `LocationEnteredEvent` first, with `via="passage"` and the passage's cell as `transition_ref`. The swung-shut events of the doors left behind come next, then a `PartyMovedEvent` for the landing cell, then the landing cell's own events. A crossing within one level emits the `PartyMovedEvent` first, and doors the party opened swing shut as after any step.
- **Cost.** A crossing costs what a step onto the landing cell costs: 10 odometer units if the party has explored it, else 30.
- **Refusal lines.** Each `validate_adventure` refusal has an exact line, because osr-editor's diagnostics match osrlib's messages by pattern. The far-cell check covers the far cell's three sides other than the passage's own, so an open passage side is reported once.
- **Schema.** A new kind value and an optional field are additive within schema 4, so `SCHEMA_VERSION` doesn't move.

## Verification

The acceptance tests pass with the marker removed, and the standing gate passes. A fresh review also ran these cases, which no test covers, and each behaved as the plan says:

- A keyed encounter on the landing cell starts after the `PartyMovedEvent`.
- A gated passage within one level takes its toll and shows no success narration.
- An overloaded party can't cross.
- A refused gate leaves the whole dungeon state unchanged.
- Arriving on a passage cell by stairs, by a trap slide, or at the entrance fires nothing.
- The far cell stays out of the player view under a lit torch.
- A replay through a passage pair matches the session.
