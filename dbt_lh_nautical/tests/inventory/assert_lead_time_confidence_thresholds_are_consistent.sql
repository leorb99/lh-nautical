select
    product_variant_id
    , location_id
    , lead_time_source
    , lead_time_observation_count
    , lead_time_confidence

from {{ ref('int_inventory_replenishment_inputs') }}

where
    (
        lead_time_source = 'product_location_history'
        and lead_time_observation_count >= 10
        and lead_time_confidence <> 'high'
    )

    or (
        lead_time_source = 'product_location_history'
        and lead_time_observation_count between 3 and 9
        and lead_time_confidence <> 'medium'
    )

    or (
        lead_time_source = 'product_location_history'
        and lead_time_observation_count between 1 and 2
        and lead_time_confidence <> 'low'
    )

    or (
        lead_time_source in ('product_history', 'global_history')
        and lead_time_confidence <> 'low'
    )

    or (
        lead_time_source = 'default_7_days'
        and lead_time_confidence <> 'assumed'
    )
