{% macro nullify_future_date(column) %}
    case 
        when {{ column }} > current_timestamp() then null
        else {{ column }}
    end
{% endmacro %}