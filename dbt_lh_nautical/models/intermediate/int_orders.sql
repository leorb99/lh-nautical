with
    orders as (
        select *
        from {{ ref('stg_orders') }}
    )

    , invoices as (
        select *
        from {{ ref('stg_fiscal_invoices') }}
    )

    , cleaned_orders as (
        select
            order_id
            , customer_id
            , salesperson_id
            , location_id

            , regexp_replace(trim(order_number), '[^a-zA-Z-0-9]', '') as order_number
            , {{ lower_trim('order_channel') }} as order_channel
            , {{ lower_trim('order_status') }} as order_status

            -- Valores financeiros
            , order_subtotal_amount
            , order_discount_amount
            , order_total_amount

            -- Nullifies future timestamps, since an order can't be placed/created/updated
            -- ahead of now; keeps the rest of the row, as this is an isolated field-level issue
            , case when placed_at > current_timestamp() then null else placed_at end as placed_at
            , case when created_at > current_timestamp() then null else created_at end as created_at
            , case when updated_at > current_timestamp() then null else updated_at end as updated_at
        from orders
    )

    , cleaned_invoices as (
        select
            fiscal_invoice_id
            , order_id
            , fiscal_invoice_nfe_number
            , fiscal_invoice_nfe_access_key
            , fiscal_invoice_series
            , fiscal_invoice_status
            , fiscal_invoice_total_amount
            , fiscal_invoice_xml_storage_uri

            -- Nullifies future timestamps, since an invoice can't be issued/created/updated
            -- ahead of now; keeps the rest of the row, as this is an isolated field-level issue
            , case when issued_at > current_timestamp() then null else issued_at end as issued_at
            , case when created_at > current_timestamp() then null else created_at end as created_at
            , case when updated_at > current_timestamp() then null else updated_at end as updated_at

            -- Internal ingestion metadata, not carried past this layer
            -- , _hash
            -- , _ingested_at
            -- , _action
        from invoices
    )

    , enriched_invoices as (
        select
            fiscal_invoice_id
            , order_id
            , fiscal_invoice_nfe_number
            , fiscal_invoice_nfe_access_key
            , fiscal_invoice_series
            , fiscal_invoice_status
            , fiscal_invoice_total_amount
            , fiscal_invoice_xml_storage_uri
            , issued_at
            , created_at
            , updated_at

            -- Conditional net revenue tracking (only count value if the invoice is valid/authorized)
            , case
                when fiscal_invoice_status = 'authorized' then fiscal_invoice_total_amount
                else 0.00
            end as authorized_fiscal_invoice_amount
        from cleaned_invoices
    )

    , joined as (
        select
            -- order attributes
            cleaned_orders.order_id
            , cleaned_orders.customer_id
            , cleaned_orders.salesperson_id
            , cleaned_orders.location_id
            , cleaned_orders.order_number
            , cleaned_orders.order_channel
            , cleaned_orders.order_status
            , cleaned_orders.order_subtotal_amount
            , cleaned_orders.order_discount_amount
            , cleaned_orders.order_total_amount
            , cleaned_orders.placed_at
            , cleaned_orders.created_at
            , cleaned_orders.updated_at

            -- fiscal invoice attributes
            , enriched_invoices.fiscal_invoice_id
            , enriched_invoices.fiscal_invoice_nfe_number
            , enriched_invoices.fiscal_invoice_nfe_access_key
            , enriched_invoices.fiscal_invoice_series
            , enriched_invoices.fiscal_invoice_status
            , enriched_invoices.fiscal_invoice_total_amount
            , enriched_invoices.fiscal_invoice_xml_storage_uri
            , enriched_invoices.authorized_fiscal_invoice_amount
            , enriched_invoices.issued_at as fiscal_invoice_issued_at
            , enriched_invoices.created_at as fiscal_invoice_created_at
            , enriched_invoices.updated_at as fiscal_invoice_updated_at

            -- Centralized rule for what counts as an effective/completed sale: the order must be paid AND have an authorized fiscal invoice.
            -- Orders paid but with a cancelled/rejected invoice are excluded, since they lack valid fiscal documentation for the sale, 
            -- even though payment was received.
            , case
                when
                    cleaned_orders.order_status = 'paid'
                    and enriched_invoices.fiscal_invoice_status = 'authorized'
                    then true
                else false
            end as is_effective_sale

            -- TODO: consider a separate flag for order_status = 'paid' with fiscal_invoice_status = 'rejected' (~1,071 orders, ~R$30.9M): 
            -- payment was received but the invoice failed fiscal validation and could still be reissued, 
            -- so this may be revenue pending regularization rather than lost.
            -- 'cancelled' invoices are a separate (~R$50M), more ambiguous case (the invoice was deliberately voided, 
            -- possibly meaning the sale itself was reversed).
            -- , case
            --     when
            --         cleaned_orders.order_status = 'paid'
            --         and enriched_invoices.fiscal_invoice_status = 'rejected'
            --         then true
            --     else false
            -- end as is_paid_sale_pending_invoice_fix 
        from cleaned_orders
        left join enriched_invoices
            on cleaned_orders.order_id = enriched_invoices.order_id
    )

select *
from joined
