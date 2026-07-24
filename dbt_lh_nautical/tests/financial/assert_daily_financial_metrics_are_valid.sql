-- Fails when daily financial counts, quantities, or rates violate their
-- expected business domains.

select *
from {{ ref('fct_daily_financial_metrics') }}
where
    effective_order_count < 0
    or effective_order_revenue_amount < 0
    or completed_refund_amount < 0
    or sold_item_quantity < 0
    or returned_item_quantity < 0
    or pending_invoice_order_count < 0
    or pending_invoice_revenue_amount < 0
    or average_ticket_amount < 0
    or return_quantity_rate < 0
    or return_quantity_rate > 1
