select
    product_variant_id
    , location_id
    , suggested_reorder_quantity

from {{ ref('fct_inventory_replenishment') }}

where suggested_reorder_quantity < 0
   or suggested_reorder_quantity is null
