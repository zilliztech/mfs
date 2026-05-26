# hubspot connector (`hubspot://`)

HubSpot CRM objects as `/<object>/records.jsonl` for each configured object
(contacts / companies / deals / tickets / ...). Records are paged from the CRM API
and flattened (the `properties` envelope is lifted to top-level fields); lazy.

object_kind = `record_collection`. **search** over `record_aggregate` chunks from
configured `text_fields` (e.g. `subject`/`content` for tickets, `dealname` for
deals); `locator` = `{id: "..."}`, `lines` null → `mfs cat <source> --locator '{"id":"..."}'`.

Config: list `objects`; per object set `text_fields`, `locator_fields` `["id"]`,
`metadata_fields`. Auth: private-app `access_token`.
