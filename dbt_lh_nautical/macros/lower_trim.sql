{% macro lower_trim(column) %}
    
    lower(trim({{ column }}))

{% endmacro %}