{% macro initcap_trim(column) %}
    
    initcap(trim({{ column }}))

{% endmacro %}