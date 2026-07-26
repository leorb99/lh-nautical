select
    product_variant_id
    , location_id
    , lead_time_source
    , lead_time_confidence
    , is_lead_time_estimated
    , lead_time_observation_count

from {{ ref('int_inventory_replenishment_inputs') }}

where
    (
        lead_time_source = 'product_location_history'
        and is_lead_time_estimated = true
    )

    or (
        lead_time_source in (
            'product_history',
            'global_history',
            'default_7_days'
        )
        and is_lead_time_estimated = false
    )

    or (
        lead_time_source = 'default_7_days'
        and (
            lead_time_observation_count <> 0
            or lead_time_confidence <> 'assumed'
        )
    )
