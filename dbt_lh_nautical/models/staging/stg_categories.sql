with 
    categories as (
        select
        cast(id as int) as category_id
        , cast(name as string) as category_name
        , cast(slug as string) as category_url_slug
        , cast(parent_category_id as int) as parent_category_id
        , cast(is_active as boolean) as is_active
        , cast(created_at as timestamp) as created_at
        , cast(updated_at as timestamp) as updated_at
        , cast(_hash as string) as _hash
        , cast(_ingested_at as timestamp) as _ingested_at
        , cast(_action as string) as _action
    from {{ source('bronze', 'categories') }}
    )

select *
from categories