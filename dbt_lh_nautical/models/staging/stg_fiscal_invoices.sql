with
    fiscal_invoices as (
        select *
        from {{ source('bronze', 'fiscal_invoices') }}
    )

    , renamed as (
        select
            cast(id as int) as fiscal_invoice_id
            , cast(order_id as int) as order_id
            , cast(nfe_number as string) as fiscal_invoice_nfe_number
            , cast(nfe_access_key as string) as fiscal_invoice_nfe_access_key
            , cast(series as string) as fiscal_invoice_series
            , cast(status as string) as fiscal_invoice_status
            , cast(total_amount as decimal(14,2)) as fiscal_invoice_total_amount
            , cast(xml_storage_uri as string) as fiscal_invoice_xml_storage_uri
            , cast(issued_at as timestamp) as issued_at
            , cast(created_at as timestamp) as created_at
            , cast(updated_at as timestamp) as updated_at

            -- internal ingestion metadata
            , cast(_hash as string) as _hash
            , cast(_ingested_at as timestamp) as _ingested_at
            , cast(_action as string) as _action

        from fiscal_invoices
    )

select *
from renamed