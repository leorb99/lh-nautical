select
    product_variant_id
    , location_id
    , selected_lead_time_days
    , lead_time_source

from {{ ref('int_inventory_replenishment_inputs') }}

where selected_lead_time_days is null
   or selected_lead_time_days < 0
   or lead_time_source is null
