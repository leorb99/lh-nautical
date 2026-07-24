with
    goods_receipts as (
        select *
        from {{ ref('stg_goods_receipts') }}
    )

    , cleaned_goods_receipts as (
        select
            goods_receipt_id
            , purchase_order_id
            , received_by_employee_id

            -- Nullifies placeholder values found via discovery: only punctuation-only values,
            -- covering both '...' and a standalone em dash ('—')
            , case
                when trim(goods_receipt_notes) rlike '^[.\\-—?]+$' then null
                else trim(goods_receipt_notes)
            end as goods_receipt_notes

            -- Nullifies future timestamps, since a receipt can't be received/created
            -- ahead of now; keeps the rest of the row, as this is an isolated field-level issue
            , case when received_at > current_timestamp() then null else received_at end as received_at
            , case when created_at > current_timestamp() then null else created_at end as created_at

            -- Preserves the original data-quality finding for auditing.
            , case
                when received_at > current_timestamp() then true
                else false
            end as is_future_receipt_date
        from goods_receipts
    )

select *
from cleaned_goods_receipts
