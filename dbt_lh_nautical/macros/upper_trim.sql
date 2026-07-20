{% macro upper_trim(column) %}
    
    upper(trim({{ column }}))

{% endmacro %}