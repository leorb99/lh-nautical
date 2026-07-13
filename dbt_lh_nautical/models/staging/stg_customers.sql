select
    id as customer_id
    , person_type
    , legal_name as customer_legal_name
    , trade_name
    , tax_id
    , state_registration
    , email as customer_email
    , phone
    , is_active
    , created_at
    , updated_at
    , _hash
    , _ingested_at
    , _action
from {{ source('bronze', 'customers') }}