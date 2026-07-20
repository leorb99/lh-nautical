with
    fiscal_invoices as (
        select *
        from {{ ref('stg_fiscal_invoices') }}
    )

    , cleaned_fiscal_invoices as (
        select
            fiscal_invoice_id
            , order_id
            , fiscal_invoice_nfe_number
            , fiscal_invoice_nfe_access_key
            , fiscal_invoice_series
            , fiscal_invoice_status
            , fiscal_invoice_total_amount
            , fiscal_invoice_xml_storage_uri
            
            -- Boolean flags based on fiscal invoice status for simplified reporting
            , case 
                when fiscal_invoice_status = 'authorized' then true 
                else false end 
                as is_nfe_authorized
            , case 
                when fiscal_invoice_status = 'cancelled' then true 
                else false end 
                as is_nfe_cancelled
            , case 
                when fiscal_invoice_status = 'rejected' then true 
                else false end 
                as is_nfe_rejected
            
            -- Conditional net revenue tracking (only count value if the invoice is valid/authorized)
            , case 
                when fiscal_invoice_status = 'authorized' then fiscal_invoice_total_amount 
                else 0.00 
            end as authorized_fiscal_invoice_amount
            
            -- Timestamps
            , issued_at
            , created_at
            , updated_at
        from fiscal_invoices
    )

select * from cleaned_fiscal_invoices