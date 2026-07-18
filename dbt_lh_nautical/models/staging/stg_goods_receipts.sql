with
    goods_receipts as (
        select *
        from {{ source('bronze','goods_receipts') }}
    )

    , renamed as (
        select
            cast(id as int) as goods_receipt_id
            , cast(purchase_order_id as int) as purchase_order_id
            , cast(received_by_employee_id as int) as received_by_employee_id
            , cast(received_at as timestamp) as received_at
            , cast(notes as string) as goods_receipt_notes
            , cast(created_at as timestamp) as created_at


            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action
        from goods_receipts
    )

select *
from renamed