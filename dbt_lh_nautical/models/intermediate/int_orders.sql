with
    orders as (
        select *
        from {{ ref('stg_orders') }}
    )

    , cleaned_order as (
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
        
            -- Criar Flags Booleanas para facilitar filtros futuros
            , case 
                when order_channel = 'ecommerce' then true 
                else false 
                end as is_ecommerce_order
            , case 
                when order_status = 'paid' then true
                else false end as is_paid_order
            , case 
                when order_status = 'cancelled' then true 
                else false 
                end as is_cancelled_order
        
            -- Calcular métricas (ex: garantir que o total bate com subtotal - desconto)
            , (order_subtotal_amount - order_discount_amount) as order_calculated_total_amount
            , case
                when order_calculated_total_amount = order_total_amount then true
                else false
                end as is_equal_total_amount
        
            , placed_at
            , created_at
            , updated_at


        from orders
    )

select * from cleaned_order