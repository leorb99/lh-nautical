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

            -- Nullifies placeholder values found via discovery: only
            -- punctuation-only values, covering both '...' and a standalone
            -- em dash ('—').
            , case
                when trim(goods_receipt_notes) rlike '^[.\\-—?]+$'
                    then null
                else trim(goods_receipt_notes)
            end as goods_receipt_notes

            -- Nullifies future timestamps while preserving the rest of the row,
            -- because these are isolated field-level data-quality issues.
            , {{ nullify_future_date('received_at') }} as received_at
            , {{ nullify_future_date('created_at') }} as created_at

            -- Preserves the original received_at data-quality finding for
            -- auditing and downstream lead-time validation.
            , case
                when received_at > current_timestamp() then true
                else false
            end as is_future_receipt_date

        from goods_receipts
    )

select *
from cleaned_goods_receipts
