with 
    products as (
        select * from {{ ref('stg_products') }}
    )

    , brands as (
        select * from {{ ref('stg_brands') }}
    )

    -- Self-join to flattening the category atribute
    , categories as (
        select 
            child.category_id as category_id
            , child.category_name as category_name
            , child.category_url_slug as category_url_slug
            , parent.category_id as parent_category_id
            , parent.category_name as parent_category_name
            
        from {{ ref('stg_categories') }} child
        left join {{ ref('stg_categories') }} parent
            on child.parent_category_id = parent.category_id
    )

    , enriched as (
        select
            p.product_id as product_id
            -- Some products lack name information, so we use a limited version of the description to name them.
            -- If the description is also null, we have a traceable placeholder.
            , coalesce(
                {{ nullify_garbage('p.product_name') }}, 
                left({{ nullify_garbage('p.product_description') }}, 40), 
                'Produto Não Identificado (ID: ' || cast(p.product_id as string) || ')'
            ) as product_name
            , {{ nullify_garbage('p.product_description') }} as product_description
            , p.product_ncm_code
            , p.product_unit_of_measure
            , p.is_active as is_product_active
            , b.brand_id as brand_id
            , b.brand_name as brand_name
            , c.category_id
            , c.category_name
            , c.category_url_slug
            , c.parent_category_id
            , c.parent_category_name
            
        from products p
        left join brands b 
            on p.brand_id = b.brand_id
        left join categories c 
            on p.category_id = c.category_id
    )

select * 
from enriched