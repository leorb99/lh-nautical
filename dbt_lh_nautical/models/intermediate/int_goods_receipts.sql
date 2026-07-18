with goods_receipts as (
    select *
    from {{ ref('stg_goods_receipts') }}
)

, intermediate_transformations as (
    select
        goods_receipt_id
        , purchase_order_id
        , received_by_employee_id
        
        -- notes are not necessary
        -- 1471 values null
        --, goods_receipt_notes
        
        -- Timestamps
        -- There are future date
        , received_at
        , created_at

        -- Calculation of latency between operational receipt and system creation in hours
        , cast(timestamp_diff(created_at, received_at, hour) as int) as system_entry_latency_hours

    from goods_receipts
)

select *
from intermediate_transformations