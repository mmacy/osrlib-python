# Views and visibility

B/X hides monster hit points, morale and reaction rolls, undiscovered secret doors, and quest triggers from players. osrlib separates player-visible data from referee data in two places: whole-session snapshots returned by [`GameSession.view`][osrlib.crawl.session.GameSession.view] and the [`Visibility`][osrlib.core.events.Visibility] field on individual events.

## Build player and referee views

Pass `Visibility.PLAYER` or `Visibility.REFEREE` to [`GameSession.view`][osrlib.crawl.session.GameSession.view] after any state change to build a frozen snapshot for that audience:

```{.python .no-run}
session.execute(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=30))

player_view = session.view(Visibility.PLAYER)
referee_view = session.view(Visibility.REFEREE)
```

[`PlayerView`][osrlib.crawl.views.PlayerView] builds its fields directly from current session state rather than from `session.event_log`, so referee-only events cannot leak hidden numbers into the snapshot. [`RefereeView`][osrlib.crawl.views.RefereeView] exposes the state that [`session_state`][osrlib.persistence.session_state] serializes into a save (including [`MonsterInstance`][osrlib.core.monsters.MonsterInstance] objects with current hit points, `flags`, `npcs`, `exploration` counters, and the unfiltered `event_log`), omitting the master seed, RNG stream states, and player-handle key. In place of the key, its `feature_handles` field maps each feature's save reference to the player handle the party sees, so a referee tool can show what the players see and send the same `feature_id` they do. Use `RefereeView` for LLM referees, test assertions, or debugging tools, and never send it to a player client:

```{.python .no-run}
# The referee sees the goblin's hit points; the player view never contains them.
referee_monster = referee_view.monsters[0]
assert referee_monster.current_hp > 0

player_group = player_view.encounter.groups[0]
assert (player_group.label, player_group.family_id, player_group.categories) == ("goblin", "goblin", ("person",))
assert player_group.count == 1
assert "current_hp" not in player_group.model_dump()
```

## Read character sheets and masked magic items

`PlayerView` includes the adventure and town names and descriptions, available town services, temple healing prices, which of those services the temple can perform for each member and what the party can pay, the party's position and facing, the elapsed clock, fatigue and deprivation status, how long the party's light and food will last, and the current `SessionMode`.

Each party member appears in `PlayerView.party` as a [`MemberView`][osrlib.crawl.views.MemberView] containing every stat needed to render a B/X character sheet: ID, name, race, class id and class name (`class_name`, which is right for a custom class too), level, title, alignment, XP and next-level XP threshold, current and maximum hit points, conditions, ability scores and modifiers, THAC0 and attack bonus, descending and ascending AC, saving throws, movement rate, languages, inventory, spell book, spell slots at each spell level (`spell_slots`), the spells the member may prepare (`preparable_spells`) and learn (`learnable_spells`), when the member chooses a spell's form (`reverses_at`), whether they can prepare spells now (`can_prepare_now`) and whether they need to sleep first (`needs_sleep_to_prepare`), memorized spells, and thief skill chances.

[`MemberView.preparable_spells`][osrlib.crawl.views.MemberView.preparable_spells] lists the spells the member may select in a [`PrepareSpells`][osrlib.crawl.commands.PrepareSpells], and [`MemberView.learnable_spells`][osrlib.crawl.views.MemberView.learnable_spells] lists the spells the member may write into their spell book with [`LearnSpell`][osrlib.crawl.commands.LearnSpell]. Build your prepare and learn dialogs from them. Both are in the order of the session's catalog, and the session checks each command with the same rules that build the lists. A spell is in `preparable_spells` exactly when the spell and the member pass every check that `PrepareSpells` makes of one copy in its normal form, and the selection as a whole still has to fit the member's slots. An arcane caster may also select the reversed form of a listed spell that has an entry in `PlayerView.reversed_spell_names`. A spell is in `learnable_spells` exactly when the spell and the member pass every check that `LearnSpell` makes. A divine caster may prepare every spell on their class's list at the spell levels where they have a slot, including the spells the adventure bundles for that list. An arcane caster may prepare the spells in their book at those levels, and may learn the spells on their list that aren't in the book, at the spell levels where the book has room. Neither list depends on the moment, so each is the same before the party has slept, during an encounter or a battle, and while the member can't act, although the session refuses the commands then. To fill a prepare dialog, group `preparable_spells` by spell level, and at each level offer no more copies than [`MemberView.spell_slots`][osrlib.crawl.views.MemberView.spell_slots] gives there.

Use three more fields of the member view to decide when to offer the dialog and which forms to put in it. [`MemberView.can_prepare_now`][osrlib.crawl.views.MemberView.can_prepare_now] is true exactly when the session accepts a `PrepareSpells` from the member now with a selection that fits the lists and the slots. It's false unless the session is in town or exploring (`session.command.wrong_mode`), while the member can't act (`session.command.member_incapacitated`), until the party has slept since the member last prepared (`magic.memorize.needs_sleep`), and for a class that casts nothing (`magic.memorize.not_a_caster`). [`MemberView.needs_sleep_to_prepare`][osrlib.crawl.views.MemberView.needs_sleep_to_prepare] states the sleep rule alone, so you can tell the table which casters need a night's rest even during an encounter. It's true for every caster in a new session, before the party's first sleep. The session reads both from its own state, so they're right in a session restored from a save, and you never need the event log to know when the party last slept. [`MemberView.reverses_at`][osrlib.crawl.views.MemberView.reverses_at] says when the member chooses the form of a spell that reverses, from the kind of caster their class is, so it's right for a custom class too. It's `"memorize"` for an arcane caster, who may select a reversed copy in `PrepareSpells`, `"cast"` for a divine caster, who prepares the normal form and may cast any memorized copy in either form, and `None` for a class that casts nothing. Offer reversed copies in the prepare dialog only when it's `"memorize"`, and both forms of a memorized copy in the cast dialog only when it's `"cast"`.

A spell id appears in six places in the view: in a member's `spell_book`, `preparable_spells`, `learnable_spells`, and `memorized_spells`, in the `spells` of an identified spell scroll in a member's `inventory`, and as the `kind` of a spell's effect in `PlayerView.effects`. Show the names in [`PlayerView.spell_names`][osrlib.crawl.views.PlayerView.spell_names], keyed by those ids, and the names of reversed forms, like *darkness* for *light*, in [`PlayerView.reversed_spell_names`][osrlib.crawl.views.PlayerView.reversed_spell_names]. Group spells by the level in [`PlayerView.spell_levels`][osrlib.crawl.views.PlayerView.spell_levels]. The view reads all three from the session's catalog, so a spell the adventure bundles has its name and level there too. They name only the spells whose ids the view shows. A spell the adventure bundles for a list that the party's classes don't cast from stays hidden. So does a spell at a spell level where the members can neither prepare nor learn it, unless a member carries an identified scroll with the spell on it. A spell on an unidentified scroll is named only when the view shows its id in another place.

To build a cast dialog, read [`PlayerView.spell_modes`][osrlib.crawl.views.PlayerView.spell_modes] and [`PlayerView.reversed_spell_modes`][osrlib.crawl.views.PlayerView.reversed_spell_modes]. They have an entry for each spell in `spell_names` and `reversed_spell_names`, with the usages of the normal form and of the reverse as [`SpellModeView`][osrlib.crawl.views.SpellModeView] models, in the order the form lists them. Each usage has the `key` that you send as `CastSpell.mode`, a battle declaration's `spell_mode`, or `UseItem.mode`, and the `display_name` that you show, such as `"Blind a creature"`. It also states whether osrlib leaves the outcome to the referee (`manual`) and what the session checks of the targets: the `targeting` mode, the fewest and most ids `targets` may hold (`min_targets` and `max_targets`), whether a target may be a cell reference (`accepts_cells`), whether a battle declaration names a group in `target_group_id` (`target_group`), and whether the usage takes one target per missile (`one_target_per_missile`). A usage that shoots one missile per target, such as *magic missile*'s, takes exactly as many targets as the caster has missiles. Read the member's number in [`MemberView.missile_counts`][osrlib.crawl.views.MemberView.missile_counts], or in `reversed_missile_counts` for a reverse, and a scroll reading's number in `scroll_missile_count`. Read how far each form reaches in [`PlayerView.spell_ranges`][osrlib.crawl.views.PlayerView.spell_ranges] and [`PlayerView.reversed_spell_ranges`][osrlib.crawl.views.PlayerView.reversed_spell_ranges], each a [`RangeSpec`][osrlib.core.spells.RangeSpec], and show the range as the page prints it from [`PlayerView.spell_range_texts`][osrlib.crawl.views.PlayerView.spell_range_texts] and [`PlayerView.reversed_spell_range_texts`][osrlib.crawl.views.PlayerView.reversed_spell_range_texts], which is the only statement of a range whose `kind` is `"special"`. A form whose `kind` is `"touch"` reaches 5', and a `per_level` range adds `per_level_feet` for each of the caster's levels. In a battle, the session refuses a target beyond the range with `magic.cast.out_of_range`. The view reads the usages from the session's catalog, so a spell the adventure bundles has the usages its author wrote.

Items work the same way. An inventory identifies a mundane stack by its template's catalog id, like `"holy_water"`, and a magic item or a gem or piece of jewellery by its instance id, like `"magic-item-0003"`, and the item events use the same ids. Show the names in [`PlayerView.item_names`][osrlib.crawl.views.PlayerView.item_names], a noun phrase for one with no article, like `"vial of holy water"`, and in [`PlayerView.item_names_plural`][osrlib.crawl.views.PlayerView.item_names_plural] for a count, so you can word "The party finds 2 vials of holy water". Both have a key for each item id that the inventories and the known piles show, and only for those ids. A stack in a pile is named from the session's catalog, and a stack a member carries from the template it embeds, so an item the adventure bundles has its name in both places. The session gives the template each carried stack embeds the catalog's display names when it starts, when a save loads, and when [`AddMember`][osrlib.crawl.commands.AddMember] adds a member, so a stack from a 2.7.0 save, whose template has no display names, shows the catalog's names in `inventory` too, and the next save writes them. A carried stack whose id the catalog lacks, such as an item another adventure bundled, keeps the names its template has. A valuable is named by its kind, `"gem"` or `"piece of jewellery"`, whereas the inventory's `valuables` contain the piece's name and value. A gem or piece of jewellery that an adventure author named, such as one a [`ValuableSpec`][osrlib.crawl.dungeon.ValuableSpec] with a `name` places in a cache, also has that name in [`PlayerView.valuable_names`][osrlib.crawl.views.PlayerView.valuable_names], keyed by the same instance id, for a member's inventory and for a known pile. Show it as written in a list, such as an inventory row. To put the piece in a sentence, use [`PlayerView.valuable_phrases`][osrlib.crawl.views.PlayerView.valuable_phrases], which has the same keys and gives each piece's phrase with its article and its case, like `"the Prior's chain of office"`, so you can word "Sam takes the Prior's chain of office." The phrase is the one the author set in the `ValuableSpec`'s `phrase`, or one osrlib makes from the name, as [Name a gem or piece of jewellery](authoring-custom-content.md#name-a-gem-or-piece-of-jewellery) describes. For an id that isn't a key, such as a piece rolled on the treasure tables, use the kind in `item_names` with an article: "Sam takes a gem." The player-visible item events, such as [`ItemAcquiredEvent`][osrlib.crawl.events.ItemAcquiredEvent], [`ItemsDroppedEvent`][osrlib.crawl.events.ItemsDroppedEvent], and [`ItemUsedEvent`][osrlib.crawl.events.ItemUsedEvent], have their own `item_names` and `item_names_plural`, keyed by the ids each event lists, and [`EquipmentDestroyedEvent`][osrlib.core.events.EquipmentDestroyedEvent] has `saved_item_names` and `saved_item_names_plural` for the items that survive. An event names each id as the view does, from the same function, with the names the party knows when the event happens, so an unidentified magic item has its mask there too. Read them when you replay an event log, or when the command used the item up, sold it, or left it on a trail, because then no view built after the command names it. A potion drunk before the party knew what it was is identified and used up in one command. Its [`ItemIdentifiedEvent`][osrlib.crawl.events.ItemIdentifiedEvent] comes first and has its true names, and the `ItemUsedEvent` that follows names it the same way.

Derived combat and exploration stats include bonuses from worn equipment (such as armour, a shield, or a ring of protection), even before the party identifies a magic item:

- **Armour class**: `armour_class` and `armour_class_ascending` reflect worn magic armour or shields immediately, because a magic item works when it's worn, while `inventory` still displays a masked description such as `"a shield with a faint aura"`. A cursed shield or suit of armour is the exception. The OSE SRD says every test shows it as enchanted with a +1 bonus until it's used in deadly combat, so until the session reveals the curse, the player view counts it as the +1 item it tests as. An unarmoured fighter with an unidentified cursed shield -2 reads AC 7, as with a shield +1, while the monsters roll against the real AC 10. The real number appears when the curse is revealed, the first time the member is attacked in a battle or when a referee sends `IdentifyItem`. [`Character.armour_class`][osrlib.core.character.Character.armour_class] and the referee view always have the real number.
- **Strength overrides**: Items that set Strength (such as gauntlets of ogre power setting STR 18, or a cursed ring of weakness setting STR 3) update [`effective_strength`][osrlib.crawl.views.MemberView.effective_strength], `melee_modifier`, and `open_doors_chance`, while `scores["str"]` preserves the rolled score. Display both `scores["str"]` and `effective_strength` if you want players to see the item's effect.
- **Timed effects**: Temporary spell and potion modifiers appear in [`PlayerView.effects`][osrlib.crawl.views.PlayerView.effects] rather than in `MemberView` stat totals. Each entry includes its remaining duration, except for potions, whose duration a B/X referee tracks secretly, so `PlayerView` reports it as unknown.

In `MemberView.inventory`, unidentified magic items use a category description from [`MagicItemCategory`][osrlib.core.items.MagicItemCategory] instead of their true name, bonus, curse state, or template ID. An enchanted weapon, piece of ammunition, suit of armour, or shield is named after one unit of the mundane item underneath it, so enchanted arrows display as `"an arrow with a faint aura"`, enchanted chainmail as `"a suit of chainmail with a faint aura"`, and enchanted armour with no recorded mundane armour displays as `"a suit of armour with a faint aura"`. `PlayerView.item_names` names an unidentified item by the same description without the article, like `"arrow with a faint aura"`, so a cursed item and the item it passes for read the same there too. When an unidentified item's display string names a weapon, such as an enchanted blade (`"a sword with a faint aura"`) or a magic staff (`"a staff"`), its inventory dict also includes the base weapon's `qualities` and (when applicable) `missile_ranges` so your UI can distinguish melee from missile attacks without revealing the enchantment. Non-weapon categories, such as `"a wand"`, omit both keys.

An identified spell scroll's entry in `MemberView.inventory` also has a `spells` key, the ids of the spells left on it in the order that [`UseItem`][osrlib.crawl.commands.UseItem] reads them, so the first is the one a `UseItem` with `spell_id` unset reads. Offer each one, and send the player's choice as `UseItem.spell_id`. Reading the scroll identifies it, and so does a referee's [`IdentifyItem`][osrlib.crawl.commands.IdentifyItem]. Nothing else does, so an unidentified scroll has no `spells` key. A cursed scroll, a scroll of protection, and a treasure map have no spells on them and no `spells` key. A dropped pile lists each thing by its id and count alone, so pick a scroll up to see its spells.

To show how much light and food the party has left, read [`PlayerView.supplies`][osrlib.crawl.views.PlayerView.supplies], a [`SuppliesView`][osrlib.crawl.views.SuppliesView]. osrlib counts it from what the living members carry by the rules the session applies, so your client needs no burn times and no feeding rules of its own. Every time in it is in rounds, like an effect's `remaining_rounds`.

- **Light**: [`light_burning_rounds`][osrlib.crawl.views.SuppliesView.light_burning_rounds] is the time left on the timed light that will burn longest. Lights burn at the same time, so it's the longest, not a sum. [`light_permanent`][osrlib.crawl.views.SuppliesView.light_permanent] is true while a light with no duration burns, such as *continual light*. [`light_reserve_rounds`][osrlib.crawl.views.SuppliesView.light_reserve_rounds] is the light the party carries unlit: its torches, and its oil flasks when a living member has a lantern. `torches`, `oil_flasks`, and `lanterns` are the counts behind it.
- **Lighting the reserve**: [`light_reserve_usable`][osrlib.crawl.views.SuppliesView.light_reserve_usable] is true when the party has what [`LightSource`][osrlib.crawl.commands.LightSource] needs to light a carried torch or lantern now: a living member who can act carries a torch, or a lantern and an oil flask, and either has a tinder box or stands in a party that already has a flame burning. The command uses the same carrying check, so in town or while exploring, the session accepts some `LightSource` for a torch or lantern exactly when the flag is true. The flag doesn't depend on the mode. During an encounter or a battle, the session refuses `LightSource` whatever the flag says, so combine the flag with `PlayerView.mode`. When the reserve isn't zero and the flag is false, the likely cause is that no member who carries a torch or a lantern has a tinder box, that the oil and the lantern are in different packs, or that the carriers can't act.
- **Food**: [`food_days`][osrlib.crawl.views.SuppliesView.food_days] is how many days pass before the first living member goes short, because each member eats from their own pack. [`food_short_first_ids`][osrlib.crawl.views.SuppliesView.food_short_first_ids] names those members. [`food_days_shared`][osrlib.crawl.views.SuppliesView.food_days_shared] is how long the rations would last shared out evenly, so when it's larger than `food_days`, suggest that the players pass rations with [`GiveItems`][osrlib.crawl.commands.GiveItems]. `rations` counts standard and iron rations together, and `rations_standard` and `rations_iron` count each kind.

A dead member's items don't count until a living member takes them, and neither do the items in a dropped pile. The summary reports the same numbers in town as in a dungeon, so decide from `PlayerView.location` whether to show it. A `PlayerView` that osrlib 2.10.0 built has no `supplies`, and it validates with `supplies` set to `None`.

To build the temple screen, read these fields of the player view:

- [`PlayerView.town_healing_prices`][osrlib.crawl.views.PlayerView.town_healing_prices] gives what each service costs.
- [`PlayerView.town_healing_funds_gp`][osrlib.crawl.views.PlayerView.town_healing_funds_gp] gives the whole gold pieces the party's purses can pay toward a fee. A service is affordable when its price is at most this number. The session counts each purse's total value in every coin type, rounded down to a gold piece, and adds up the whole party, dead members included, so don't count the coins yourself.
- [`PlayerView.town_healing_rejections`][osrlib.crawl.views.PlayerView.town_healing_rejections] is keyed by the member's id and then by the service, like `"raise_dead"`, and covers every member, the dead included. An empty tuple means some temple cleric can perform the service for the member. Otherwise the tuple contains the rejections that a [`PurchaseHealing`][osrlib.crawl.commands.PurchaseHealing] gets, with the codes and params the command returns, so disable the service and show the reason. The code you'll meet is `town.healing.no_effect`, for a service that would do nothing, and its `reason` param names the case, such as `full_hit_points`, `dead`, or `not_dead`.
- [`PlayerView.town_healing_mode_rejections`][osrlib.crawl.views.PlayerView.town_healing_mode_rejections] answers the same way for each usage of a service that the temple sells in more than one usage, keyed by the member's id, the service, and the usage's key, which you send as `PurchaseHealing.spell_mode`. `town_healing_rejections` answers for each service's first usage. *Cure light wounds* is the service in this map: its `heal` usage restores hit points, and its `cure_paralysis` usage ends a member's paralysis, so offer "cure paralysis" when that usage's tuple is empty. [`PlayerView.town_healing_mode_names`][osrlib.crawl.views.PlayerView.town_healing_mode_names] gives each usage's display name to label it with.
- [`PlayerView.town_healing_caster_levels`][osrlib.crawl.views.PlayerView.town_healing_caster_levels] gives, for each service with an empty tuple, the lowest cleric level that can perform it. Send that number as `PurchaseHealing.caster_level`. It's the service's minimum level except for *raise dead* on a member who died a day or more ago, whose reach needs a higher cleric. The price is the same at every level, and a higher level gives the party nothing more, so you don't need a level control.

```json
{
  "town_healing_rejections": {
    "character-0001": {
      "cure_light_wounds": [],
      "raise_dead": [{"code": "town.healing.no_effect", "params": {"item": "raise_dead", "character": "character-0001", "reason": "not_dead"}}]
    },
    "character-0002": {
      "cure_light_wounds": [{"code": "town.healing.no_effect", "params": {"item": "cure_light_wounds", "character": "character-0002", "reason": "dead"}}],
      "raise_dead": []
    }
  },
  "town_healing_caster_levels": {
    "character-0001": {"cure_light_wounds": 2, "cure_serious_wounds": 6},
    "character-0002": {"raise_dead": 8}
  },
  "town_healing_funds_gp": 120
}
```

The example shows two of the services in `town_healing_rejections`. In it, the first member is alive and wounded, and the second member died 3 days ago. The temple can heal the first member with a level 2 cleric, and raise the second with a level 8 cleric, because *raise dead* reaches back 4 days for each level above 7th. The party's purses can pay 120 gp, so it can afford *cure light wounds* and *cure serious wounds* but not *raise dead*. The rejections and the levels don't depend on the fee, because the session refuses a purchase the party can't pay for with `items.purchase.insufficient_funds` before it makes the other checks. Both maps are empty unless the session is in the `"town"` mode, and the funds are stated in every mode. A `PlayerView` that osrlib 2.10.0 built has none of these three fields, and it validates with both maps empty and the funds set to `None`.

## Render maps, encounters, and battle positions

`PlayerView` exposes explored dungeon geometry through [`ExploredLevelView`][osrlib.crawl.views.ExploredLevelView] and [`EdgeView`][osrlib.crawl.views.EdgeView]: every cell the party has visited, every cell previously illuminated by the party's light (stored in [`DungeonState.seen`][osrlib.crawl.dungeon.DungeonState.seen] for automapping), and cells currently lit from the party's position. Undiscovered secret doors appear as plain walls. A door's [`EdgeView.trap_state`][osrlib.crawl.views.EdgeView.trap_state] is `"found"` or `"sprung"` once the party knows that a room trap of an area beside the door springs when someone opens it. The door shows the trap when a [`Search`][osrlib.crawl.commands.Search] found the trap through that door, when opening that door sprang it, or when the party has walked the trapped area's cell beside the door. So a door shows a trap the party found from the corridor before anyone enters the room, and the view shows it again after a reconnect or a restored save. A trapped room's other doors stay `None` until the party walks the room's cell beside them, and the field never names or describes the room.

Each level also reports the authored areas the party entered, with only walked area cells. An entered area's `trap_state` is `"found"` or `"sprung"` once the party has found or sprung the room's trap, and `None` before, so a room whose trap the party hasn't met looks like a room without one. When areas overlap, the first area in the level's list owns the shared cell and only that owner can enter through it. A known custom feature or treasure cache appears as a [`MapFeatureView`][osrlib.crawl.views.MapFeatureView] on its visible cell. An area-bound feature appears after the party enters that area, and a construction trick appears only after search marks it in `found_tricks`. An explicit feature on a shared cell belongs to the owning area only. Use the feature's opaque `id` in `feature_id`; the feature list never exposes the authored or generated raw id. The events use the same handles. A [`TrapEvent`][osrlib.crawl.events.TrapEvent] about a treasure trap has its cache's handle in `feature_id`, and a [`SearchCompletedEvent`][osrlib.crawl.events.SearchCompletedEvent] has the handles of the construction tricks it found in `feature_ids`. Match them against `MapFeatureView.id` to tell which listed feature an event is about. The fields list the safe kind, description, location, area id after entry, trap state (`"found"`, `"sprung"`, or `"removed"`), and `emptied`, which is true for an authored treasure cache the party has emptied. They never include cache contents, trap or trigger mechanics, destinations, gates, or an unannounced secret. An emptied cache stays in the list under the same `id`, so you can draw it opened, but `TakeTreasure` refuses it with `exploration.feature.emptied`. The description is the author's [`FeatureSpec.description`][osrlib.crawl.dungeon.FeatureSpec.description], written for the players, and the feature's [`referee_notes`][osrlib.crawl.dungeon.FeatureSpec.referee_notes] never appear in the player view. [Describe a treasure cache for the players](authoring-custom-content.md#describe-a-treasure-cache-for-the-players) shows how to write each field.

Generated area caches appear after their cells become known. The session removes a generated cache when the party takes it, so the cache leaves the list. A generated cache's `area_id` names the area that owns its cell once the party has entered that area. A generated lair cache stays out of the view and refuses feature commands until the party resolves the area's keyed encounter. [`TransitionView`][osrlib.crawl.views.TransitionView] exposes the kind and safe position of each transition on a known cell, but not its destination or gate. `ExploredLevelView.entrance` appears only when the party knows the entrance cell, and `PlayerView.can_travel_to_town` is true only when the party can issue [`TravelToTown`][osrlib.crawl.commands.TravelToTown] from there. Known dropped piles stay in `PlayerView.piles`. Each [`PileView`][osrlib.crawl.views.PileView] lists its things twice, in the same order: `items` as display strings like `"holy_water×2"`, and `contents` as [`PileItemView`][osrlib.crawl.views.PileItemView] entries with the id and the count, which you name from `PlayerView.item_names`.

`PlayerView.features_here` lists the handles that [`TakeTreasure`][osrlib.crawl.commands.TakeTreasure], [`InspectTreasure`][osrlib.crawl.commands.InspectTreasure], and [`RemoveTreasureTrap`][osrlib.crawl.commands.RemoveTreasureTrap] accept from where the party stands, in the order of the level's `features`. The session resolves a command's `feature_id` against the same caches, so offer those handles rather than working out reach yourself. The list is empty unless the session is exploring. To tell whether a listed cache is emptied, read its `MapFeatureView.emptied`. `RemoveTreasureTrap` accepts a listed cache only when its `trap_state` is `"found"`, and it refuses any other with `exploration.feature.unknown`, so its answer doesn't say whether a cache is trapped.

`PlayerView.emptied_caches` keeps its raw feature references. A raw authored feature id can appear there after the party empties the cache, but it never appears in `MapFeatureView.id`, and a client reads `MapFeatureView.emptied` instead.

During an active encounter or battle, `PlayerView.encounter` provides an [`EncounterView`][osrlib.crawl.views.EncounterView] with [`EncounterGroupView`][osrlib.crawl.views.EncounterGroupView] entries for each monster group:

- **Group identity and status**: Each group includes its ID, living `count`, distance, visible conditions with counts, whether it's running from the battle ([`fleeing`][osrlib.crawl.views.EncounterGroupView.fleeing]), and whether it has got away ([`fled`][osrlib.crawl.views.EncounterGroupView.fled]), without exposing hit points. A group that's `fleeing` and not `fled` is still a legal target. The session emits a [`MonsterEscapedEvent`][osrlib.crawl.events.MonsterEscapedEvent] for a group in the command result in which its `fled` becomes true, and in no other result, so you can show the group getting away from the event stream alone. Display [`label`][osrlib.crawl.views.EncounterGroupView.label] for one creature (such as `"goblin"`) and [`label_plural`][osrlib.crawl.views.EncounterGroupView.label_plural] for multiple creatures (`"goblins"`). Because custom encounters can set arbitrary display labels, branch on [`family_id`][osrlib.crawl.views.EncounterGroupView.family_id] (for example, `"hellhound"` across Hit Dice variants) and [`categories`][osrlib.crawl.views.EncounterGroupView.categories] (for example, `"undead"`) in code rather than matching display text.
- **Individual targets**: [`members`][osrlib.crawl.views.EncounterGroupView.members] lists every monster or NPC adventurer in the group's existing order, including dead and fled members. Each frozen [`EncounterMemberView`][osrlib.crawl.views.EncounterMemberView] contains only `id` and `status`. The status is `"defeated"` when dead, `"fled"` when alive in a group that has left, and `"alive"` otherwise. Individual conditions and private statistics don't appear. IDs match events and stay the same after save/load or a group split, so you can map an event's creature ID to its current group. Select an `"alive"` ID for a spell or scroll through [`BattleDeclaration.targets`][osrlib.crawl.commands.BattleDeclaration.targets] before receiving a combat event. Range, touch, visibility, and the spell mode's checks still apply. A running group stays targetable until it leaves. An unknown, out-of-encounter, dead, or fled enemy ID gets `magic.cast.unknown_target` without spending time or changing state. Refresh the view before choosing another target. The same rule holds outside a battle: a scroll read or a staff of healing with [`UseItem`][osrlib.crawl.commands.UseItem] during an encounter names an `"alive"` id, and once the encounter ends, no monster or NPC adventurer is a target until another encounter opens. Group-targeted area and Hit Dice budget spells still use `target_group_id`.
- **Evasion**: [`evade_rejections`][osrlib.crawl.views.EncounterView.evade_rejections] gives, for each value of [`Evade.drop`][osrlib.crawl.commands.Evade.drop], the rejections that an [`Evade`][osrlib.crawl.commands.Evade] with that drop would get now. An empty tuple means that the session accepts it. Use it to disable a choice on your flee menu, such as scattering coins or dropping food, and to show the reason, rather than working out from the purses and packs which members and items count. The map is empty in a battle, because the session refuses every `Evade` there.
- **Round eligibility**: `EncounterView` lists which party members must submit declarations (`declarers`), which members cannot move (`immobile`), and which members are reloading (`reloading`). [`party_free_round`][osrlib.crawl.views.EncounterView.party_free_round] is true in the round the party gets by surprising the monsters.
- **Hiding**: [`can_hide`][osrlib.crawl.views.EncounterView.can_hide] lists the members who can declare `hide` this round, and [`hiding`][osrlib.crawl.views.EncounterView.hiding] lists the members who are hiding. Neither list says whether a hide worked, because the session rolls the thief's skills in secret. The players learn the outcome from the back-stab's [`AttackRolledEvent.back_stab`][osrlib.core.events.AttackRolledEvent.back_stab], and [`HideAttemptedEvent`][osrlib.crawl.events.HideAttemptedEvent] reports each attempt without its roll.
- **Back-stabs**: [`can_back_stab`][osrlib.crawl.views.EncounterView.can_back_stab] lists the members who can declare an `attack` with [`back_stab`][osrlib.crawl.commands.BattleDeclaration.back_stab] this round. Offer the back-stab in your battle menu to the members in it, rather than working it out from the class, `party_free_round`, and `hiding`. The list comes from the same checks the session runs, so it's correct for a custom class too, and the session never refuses a member in it with `battle.declaration.back_stab_no_ability` or `battle.declaration.back_stab_target_aware`. The session can still refuse a back-stab by a member in it for the declaration itself, such as a missile weapon, a group out of reach, or, in the free round, a place outside the group's front rank. Like `hiding`, the list includes every member who has hidden, whatever their roll, so it never tells the players whether a hide worked.
- **Melee reach and range track**: Check [`EncounterGroupView.front_rank`][osrlib.crawl.views.EncounterGroupView.front_rank] to see which party members can melee that group on the current round without moving (do not use `EncounterView.front_rank` for melee eligibility, because `EncounterView.front_rank` lists the party's leading row toward the nearest group regardless of distance). On each group, [`member_distance_feet`][osrlib.crawl.views.EncounterGroupView.member_distance_feet] gives every member's distance to that group, while [`EncounterView.advance_feet`][osrlib.crawl.views.EncounterView.advance_feet] gives each member's distance forward of the starting party line. Two members must be within 5 feet of each other on `advance_feet` to hand over items, administer a potion, or use a touch spell or staff of healing.
- **Movement, frontage, and sight**: A member can close in and make a melee attack in the same round when their distance to the group is at most [`MemberView.encounter_rate_feet`][osrlib.crawl.views.MemberView.encounter_rate_feet] plus 5 feet. [`formation_width`][osrlib.crawl.views.EncounterView.formation_width] caps how many combatants fit side by side in a front rank (limiting a monster group's melee attackers to the smaller of `count` and `formation_width`). Each group's [`lit`][osrlib.crawl.views.EncounterGroupView.lit] and [`seen_by`][osrlib.crawl.views.EncounterGroupView.seen_by] fields report whether the party's light reaches the group and which members can see it.

To validate battle choices interactively as a player builds a round's orders, pass the declarations chosen so far to [`preview_battle_round`][osrlib.crawl.battle.preview_battle_round]. It runs the same checks as [`ResolveBattleRound`][osrlib.crawl.commands.ResolveBattleRound] without advancing combat, returning per-declaration rejections, projected member positions, and updated `front_rank` lists so your UI can disable invalid actions. The preview's [`can_hide`][osrlib.crawl.battle.BattleRoundPreview.can_hide] and [`can_back_stab`][osrlib.crawl.battle.BattleRoundPreview.can_back_stab] list who could still declare a `hide` or a back-stab beside the choices made so far, each member judged as if that declaration took the place of their own. So another member's planned fighting withdrawal can take a thief out of `can_hide`, and another member's planned `close` can put one in it. Every rejection of a declaration, from the preview or from `ResolveBattleRound`, has the declaring member's id as its `character` param, so a client with several players can tell whose choice to change.

To mark which targets each action on a member's menu reaches before the player picks one, pass the actions in `candidates`, each a [`BattleCandidate`][osrlib.crawl.battle.BattleCandidate] that holds a declaration without its target and the target ids to judge it at. The preview's [`candidates`][osrlib.crawl.battle.BattleRoundPreview.candidates] gives the rejections at each target, judged as if the action took the place of the member's own planned declaration, so you can disable a target and show the session's reason. It also gives each attack's [`attack_kinds`][osrlib.crawl.battle.BattleCandidateJudgement.attack_kinds] entry, which says whether the member would make a melee or a missile attack there, so you can label a dagger's close and attack that stops short as a throw. Name `weapon_id` in every attack candidate, so the answer is about the weapon on the menu.

## Filter events, quests, and the journal

If your front end streams or narrates events from `CommandResult.events` or `session.event_log`, filter on `event.visibility == Visibility.PLAYER` before showing anything to players. Most events (such as movement, opening doors, and attack damage) use `Visibility.PLAYER`. Referee-only rolls (such as morale checks, reaction rolls, wandering-monster checks, secret detection rolls, and [`HitPointsReportedEvent`][osrlib.core.events.HitPointsReportedEvent]) use `Visibility.REFEREE`.

Authored triggers, notes, and quests follow the same visibility boundary:

- **Journal vs. triggers and notes**: [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent] uses `Visibility.PLAYER` and appends to `PlayerView.journal` ([`JournalEntry`][osrlib.crawl.session.JournalEntry] items ordered by discovery with their clock timestamps). [`TriggerFiredEvent`][osrlib.crawl.events.TriggerFiredEvent], [`NoteRecordedEvent`][osrlib.crawl.events.NoteRecordedEvent], and flag updates use `Visibility.REFEREE` and appear only on `RefereeView`:

```{.python .no-run}
session.execute(MarkTriggerFired(trigger_id="lever-east"))
session.execute(AddJournalEntry(text="The lever grinds.", source="trigger:lever-east"))
session.execute(RecordNote(text="The east lever is the only one that answers."))

# Views are frozen snapshots, so call view() again to read the updated state.
journal_view = session.view(Visibility.PLAYER)
referee_after = session.view(Visibility.REFEREE)

assert [entry.text for entry in journal_view.journal][-1] == "The lever grinds."
assert "lever-east" not in journal_view.model_dump_json()
assert referee_after.fired_triggers == ("lever-east",)
```

- **Gate refusals**: When a gated door or transition blocks the party, `CommandResult.rejections` includes the authored `refusal` text in `params` (see [Gates, triggers, and quests](gates-triggers-quests.md)).
- **Quests**: `PlayerView.quests` includes [`QuestView`][osrlib.crawl.views.QuestView] entries only for **active** quests in document order, omitting inactive quests (whose activation conditions remain hidden) and completed quests (whose history remains in `PlayerView.journal`). Within an active quest, only **revealed** objectives appear, with `ObjectiveView.state` set to `"incomplete"` or `"complete"`. Hidden objectives, trigger clauses, rewards, and narrator `guidance` fields never appear in `PlayerView`:

```{.python .no-run}
# Active quests only, revealed objectives only, and none of the setup behind them.
quest_view = journal_view.quests[0]
assert (quest_view.id, quest_view.speaker) == ("the-lamps", "Sister Halda")
assert [entry.id for entry in quest_view.objectives] == ["find-the-lever"]
assert quest_view.objectives[0].name == "Find the lever"  # falls back to id when name is omitted
assert "name-the-dead" not in journal_view.model_dump_json()
```

When a quest activates, reveals an objective, completes an objective, or finishes, the session appends that beat to `session.journal` and reports it on the quest lifecycle event without emitting a separate [`JournalEntryAddedEvent`][osrlib.crawl.events.JournalEntryAddedEvent]. If you update a journal panel incrementally from events, watch `session.journal.entry_added`, `session.quest.activated`, `session.quest.objective_revealed`, `session.quest.objective_completed`, and `session.quest.completed`. Otherwise, render `PlayerView.journal` directly after each command.

## Enforce visibility on the server

In a networked game, treat the player/referee split as an authority boundary. Keep `GameSession` and `RefereeView` on the server: accept a command per request, call `session.execute(command)` on the server, and return only `session.view(Visibility.PLAYER)` along with events filtered to `Visibility.PLAYER`. Give each new session an independent random `player_handle_key` of at least 32 bytes, and persist it in the server-side save so restored sessions resolve the same handles. Sending `RefereeView`, a save, or running `GameSession` on an untrusted client exposes referee state, the master seed, and the key. See [The FastAPI pattern](../front-ends/fastapi-pattern.md) for a reference HTTP server layout.

## The complete example

```python
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.events import Visibility
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.commands import (
    AddJournalEntry,
    EnterDungeon,
    MarkTriggerFired,
    RecordNote,
    SessionMode,
    SetFlag,
    SpawnMonsters,
)
from osrlib.crawl.dungeon import DungeonSpec, LevelSpec
from osrlib.crawl.interpreter import Interpreter
from osrlib.crawl.narrative import NarrativeBlock
from osrlib.crawl.party import Party
from osrlib.crawl.quests import ObjectiveSpec, QuestSpec, TriggerClause
from osrlib.crawl.session import GameSession
from osrlib.crawl.triggers import DungeonEnteredPattern, FlagSetPattern

rules = Ruleset()
creation = RngStreams(master_seed=13).get(CHARACTER_CREATION_STREAM)
hero = create_character(
    name="Rurik",
    class_id="fighter",
    alignment=Alignment.LAWFUL,
    ruleset=rules,
    stream=creation,
)
party = Party(members=[hero.character])

level = LevelSpec(number=1, width=1, height=1, entrance=(0, 0))
crypt = DungeonSpec(id="crypt", name="The Old Crypt", levels=(level,))
town = TownSpec(name="Threshold", travel_turns={"crypt": 1})

# One errand, offered at the threshold: one objective the party is told about, and
# one it is not.
errand = QuestSpec(
    id="the-lamps",
    name="The Unlit Lamps",
    activation=TriggerClause(pattern=DungeonEnteredPattern(dungeon_id="crypt")),
    objectives=(
        ObjectiveSpec(
            id="find-the-lever",
            name="Find the lever",
            when=TriggerClause(pattern=FlagSetPattern(key="crypt.lever")),
            narrative=NarrativeBlock(progress="The lamps come up one by one."),
        ),
        ObjectiveSpec(id="name-the-dead", when=TriggerClause(pattern=FlagSetPattern(key="crypt.name")), hidden=True),
    ),
    narrative=NarrativeBlock(
        offer="Light the crypt's lamps before the moon sets.",
        speaker="Sister Halda",
    ),
)
adventure = Adventure(name="A First Delve", town=town, dungeons=(crypt,), quests=(errand,))
session = GameSession.new(party, adventure, seed=13)
session.register_listener(Interpreter(session))

session.execute(EnterDungeon(dungeon_id="crypt"))

# The referee creates a lone goblin and opens an encounter at 30 feet.
result = session.execute(SpawnMonsters(template_id="goblin", count_fixed=1, distance_feet=30))
assert result.accepted
assert session.mode is SessionMode.ENCOUNTER

player_view = session.view(Visibility.PLAYER)
referee_view = session.view(Visibility.REFEREE)

# The referee sees the goblin's hit points; the player view never contains them.
referee_monster = referee_view.monsters[0]
assert referee_monster.current_hp > 0

player_group = player_view.encounter.groups[0]
assert (player_group.label, player_group.family_id, player_group.categories) == ("goblin", "goblin", ("person",))
assert player_group.count == 1
assert "current_hp" not in player_group.model_dump()

# A trigger fires: it is marked, it writes a journal beat, and the referee annotates it.
session.execute(MarkTriggerFired(trigger_id="lever-east"))
session.execute(AddJournalEntry(text="The lever grinds.", source="trigger:lever-east"))
session.execute(RecordNote(text="The east lever is the only one that answers."))

journal_view = session.view(Visibility.PLAYER)
referee_after = session.view(Visibility.REFEREE)

# The beat is for the table; the trigger that produced it is referee-only setup.
assert [entry.text for entry in journal_view.journal][-1] == "The lever grinds."
assert "lever-east" not in journal_view.model_dump_json()
assert referee_after.fired_triggers == ("lever-east",)

# The quest activated at the threshold, and its offer opened the journal.
quest_view = journal_view.quests[0]
assert (quest_view.id, quest_view.name) == ("the-lamps", "The Unlit Lamps")
assert quest_view.narrative == "Light the crypt's lamps before the moon sets."
assert quest_view.speaker == "Sister Halda"
assert journal_view.journal[0].text == quest_view.narrative

# Only the revealed objective is projected, and none of the setup behind it.
assert [(entry.id, entry.name, entry.state) for entry in quest_view.objectives] == [
    ("find-the-lever", "Find the lever", "incomplete")
]
blob = journal_view.model_dump_json()
assert "name-the-dead" not in blob  # a hidden objective has no view at all
assert "pattern_type" not in blob and "crypt.lever" not in blob

# The flag that objective watches: the quest completes it, journals its beat, and
# reports the beat through its own event, so no journal event follows.
lit = session.execute(SetFlag(key="crypt.lever", value=True))
codes = [event.code for event in lit.events]
assert "session.quest.objective_completed" in codes
assert "session.journal.entry_added" not in codes
assert session.journal[-1].text == "The lamps come up one by one."

after = session.view(Visibility.PLAYER)
assert [(entry.id, entry.state) for entry in after.quests[0].objectives] == [("find-the-lever", "complete")]
assert session.quests["the-lamps"].status == "active"  # the hidden objective is still open
```

## Where next

- [Sessions, commands, and events](sessions-commands-events.md) - executing commands and reading `CommandResult`.
- [Listeners and flags](listeners-and-flags.md) - referee-only flags and listener state.
- [Gates, triggers, and quests](gates-triggers-quests.md) - authoring journal beats, quest objectives, and gate refusal messages.
- [The FastAPI pattern](../front-ends/fastapi-pattern.md) - serving `PlayerView` over HTTP while keeping `GameSession` on the server.
- [LLM referees](../front-ends/llm-referees.md) - driving narration from `RefereeView` and the full event log.
