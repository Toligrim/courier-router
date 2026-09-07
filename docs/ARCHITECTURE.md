# Architecture notes

## Invariants
- LLM never creates coordinates or computes route order.
- Router computes travel matrix + road geometry.
- OR-Tools computes order and schedule.
- Low-confidence geocodes are visible warnings.
- Phone/payment data never enter LLM calls.
- Provider adapters are replaceable.

## Current MVP
DaData + ORS + OR-Tools + SQLite + custom Pillow/OSM tile renderer.

## Local target
DaData/Nominatim + local OSRM + OR-Tools + SQLite + renderer.

## Pickup/delivery
The domain model contains `shipment_id`, but v0.1 treats rows as independent jobs.
Do not infer a shipment merely from operation type. Add explicit pairing rules after
business confirmation.
