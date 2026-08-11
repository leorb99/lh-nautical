{% macro trim_collapse_whitespace(column) %}

    regexp_replace(trim({{ column }}), '\\s+', ' ')

{% endmacro %}
