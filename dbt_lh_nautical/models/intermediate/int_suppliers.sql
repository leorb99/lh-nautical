with
    suppliers as (
        select *
        from {{ ref('stg_suppliers') }}
    )

    , cleaned_suppliers as (
        select
            supplier_id

            -- Trims and collapses internal whitespace only, same reasoning
            -- as brand_name/category_name: capitalization macros were
            -- tried elsewhere for similar entity-name fields and rejected,
            -- since they corrupt real values (abbreviations, hyphenated
            -- names). No such risk with a plain trim.
            , {{ trim_collapse_whitespace('supplier_legal_name') }}
                as supplier_legal_name

            -- One confirmed bad value found via discovery: a trade name of
            -- exactly "das" (a standalone Portuguese preposition, not a
            -- real trade name), for the supplier whose legal name is
            -- "das Neves - ME". Nulled explicitly instead of relying on a
            -- general rule, since this is a single confirmed case, not
            -- validated as a placeholder pattern.
            , case
                when {{ trim_collapse_whitespace('supplier_trade_name') }} = 'das'
                    then null
                else {{ trim_collapse_whitespace('supplier_trade_name') }}
            end as supplier_trade_name

            , supplier_country
            , supplier_tax_id
            , supplier_tax_id_type
            , supplier_email
            , supplier_phone

            , {{ trim_collapse_whitespace('supplier_contact_name') }}
                as supplier_contact_name

            , is_active
            , created_at
            , updated_at
        from suppliers
    )

select *
from cleaned_suppliers
